import asyncio
import json
import logging
from typing import Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Ticket, TicketPriority, TicketStatus
from app.db.session import Database
from app.llm.models import ToolCall
from app.observability.logging import JsonFormatter
from app.repositories.tenant import TenantRepository
from app.repositories.ticket import TicketRepository
from app.services.tickets import TicketService
from app.tools import ToolExecutionContext, ToolExecutionPolicy, ToolExecutor, ToolRegistry
from app.tools.builtin import build_tool_registry
from app.tools.builtin.ticket_create import ticket_create_tool
from app.tools.models import ToolErrorCategory

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def seed(database: Database) -> tuple[UUID, UUID, UUID, UUID]:
    async with database.transaction() as session:
        repo = TenantRepository(session)
        a = await repo.create(slug="runtime-a", name="A")
        b = await repo.create(slug="runtime-b", name="B")
        tickets = TicketRepository(session)
        own = await tickets.create(tenant_id=a.id, title="A open")
        await tickets.create(tenant_id=a.id, title="A closed", status=TicketStatus.CLOSED)
        other = await tickets.create(
            tenant_id=b.id, title="B private", description="B confidential"
        )
        return a.id, b.id, own.id, other.id


def context(tenant_id: UUID, *, write: bool = False) -> ToolExecutionContext:
    return ToolExecutionContext(tenant_id=tenant_id, policy=ToolExecutionPolicy(allow_writes=write))


@pytest.mark.parametrize(
    "mode", ["list", "status", "limit", "own_id", "foreign_id", "missing_id", "id_status_mismatch"]
)
async def test_search_tenant_predicates_filters_and_bounds(database: Database, mode: str) -> None:
    a, b, own, other = await seed(database)
    arguments: dict[str, object] = {}
    expected = {"A open", "A closed"}
    if mode == "status":
        arguments = {"status": "closed"}
        expected = {"A closed"}
    elif mode == "limit":
        arguments = {"limit": 1}
    elif mode in {"own_id", "foreign_id", "missing_id", "id_status_mismatch"}:
        ticket_id = (
            own
            if mode in {"own_id", "id_status_mismatch"}
            else other
            if mode == "foreign_id"
            else uuid4()
        )
        arguments = {"ticket_id": str(ticket_id)}
        expected = {"A open"} if mode == "own_id" else set()
        if mode == "id_status_mismatch":
            arguments["status"] = "closed"
    assert database.engine is not None
    commits: list[bool] = []

    def on_commit(connection: object) -> None:
        commits.append(True)

    event.listen(database.engine.sync_engine, "commit", on_commit)
    try:
        result = await ToolExecutor(build_tool_registry(database)).execute(
            ToolCall.model_validate(
                {"id": "search", "name": "ticket_search", "arguments": arguments}
            ),
            context(a),
        )
    finally:
        event.remove(database.engine.sync_engine, "commit", on_commit)
    assert not commits, "READ_ONLY tools must not commit"
    assert result.success and result.data is not None
    data = result.data["tickets"]
    assert isinstance(data, list)
    titles = {row["title"] for row in data if isinstance(row, dict)}
    assert len(data) == 1 and titles <= expected if mode == "limit" else titles == expected
    assert (
        "B private" not in result.model_dump_json()
        and "B confidential" not in result.model_dump_json()
    )
    async with database.transaction() as session:
        intact = await TicketRepository(session).get_by_id(tenant_id=b, ticket_id=other)
        assert intact is not None and intact.description == "B confidential"


@pytest.mark.parametrize("name", ["ticket_search", "ticket_create"])
async def test_injected_tenant_rejected_with_real_database(database: Database, name: str) -> None:
    a, b, _, other = await seed(database)
    arguments: dict[str, object] = {"tenant_id": str(b)}
    if name == "ticket_create":
        arguments["title"] = "Stolen ownership"
    result = await ToolExecutor(build_tool_registry(database)).execute(
        ToolCall.model_validate({"id": "inject", "name": name, "arguments": arguments}),
        context(a, write=True),
    )
    assert result.error is not None and result.error.category is ToolErrorCategory.INPUT_VALIDATION
    async with database.transaction() as session:
        repo = TicketRepository(session)
        assert len(await repo.list(tenant_id=a)) == 2
        assert [ticket.id for ticket in await repo.list(tenant_id=b)] == [other]


async def test_create_commits_only_trusted_tenant_and_logs_no_private_data(
    database: Database,
    caplog: pytest.LogCaptureFixture,
) -> None:
    a, b, _, other = await seed(database)
    caplog.set_level(logging.INFO)
    result = await ToolExecutor(build_tool_registry(database)).execute(
        ToolCall(
            id="create",
            name="ticket_create",
            arguments={
                "title": "Private support title",
                "description": "Enterprise confidential body",
                "priority": "high",
            },
        ),
        context(a, write=True),
    )
    assert result.success and result.data is not None
    ticket_id = UUID(str(result.data["id"]))
    async with database.transaction() as session:
        repo = TicketRepository(session)
        created = await repo.get_by_id(tenant_id=a, ticket_id=ticket_id)
        assert created is not None and created.tenant_id == a
        assert created.priority is TicketPriority.HIGH and created.status is TicketStatus.OPEN
        assert created.description == "Enterprise confidential body"
        assert await repo.get_by_id(tenant_id=b, ticket_id=ticket_id) is None
        assert [t.id for t in await repo.list(tenant_id=b)] == [other]
    formatted = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    raw = repr([record.__dict__ for record in caplog.records])
    for private in ["Enterprise confidential body", "Private support title"]:
        assert private not in formatted + raw
    assert "description" not in result.model_dump_json()
    events = [
        json.loads(JsonFormatter().format(r))
        for r in caplog.records
        if r.name == "app.tools.executor"
    ]
    assert events[-1]["tool_outcome"] == "success" and events[-1]["tool_effect"] == "write"


async def test_denied_write_never_opens_transaction(database: Database) -> None:
    a, b, _, other = await seed(database)
    assert database.engine is not None
    begins: list[bool] = []

    def on_begin(connection: object) -> None:
        begins.append(True)

    event.listen(database.engine.sync_engine, "begin", on_begin)
    try:
        result = await ToolExecutor(build_tool_registry(database)).execute(
            ToolCall(id="deny", name="ticket_create", arguments={"title": "Denied"}),
            context(a),
        )
    finally:
        event.remove(database.engine.sync_engine, "begin", on_begin)
    assert result.error is not None and result.error.category is ToolErrorCategory.POLICY
    assert begins == []
    async with database.transaction() as session:
        repo = TicketRepository(session)
        assert len(await repo.list(tenant_id=a)) == 2
        assert [t.id for t in await repo.list(tenant_id=b)] == [other]


@pytest.mark.parametrize("mode", ["error", "timeout", "cancel", "invalid_output"])
async def test_flushed_create_rolls_back_without_retry(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    mode: Literal["error", "timeout", "cancel", "invalid_output"],
) -> None:
    a, b, _, other = await seed(database)
    original = TicketRepository.create
    original_timeout = asyncio.timeout
    deadlines: list[asyncio.Timeout] = []

    def controlled_timeout(delay: float | None) -> asyncio.Timeout:
        deadline = original_timeout(10)
        deadlines.append(deadline)
        return deadline

    entered = asyncio.Event()
    calls = 0

    async def fail_after_flush(
        self: TicketRepository,
        *,
        tenant_id: UUID,
        title: str,
        description: str = "",
        status: TicketStatus = TicketStatus.OPEN,
        priority: TicketPriority = TicketPriority.MEDIUM,
    ) -> Ticket:
        nonlocal calls
        calls += 1
        ticket = await original(
            self,
            tenant_id=tenant_id,
            title=title,
            description=description,
            status=status,
            priority=priority,
        )
        entered.set()
        if mode == "error":
            raise RuntimeError(
                "postgresql://hidden-driver-payload Authorization private-description"
            )
        if mode == "timeout":
            deadlines[0].reschedule(asyncio.get_running_loop().time())
        if mode in {"timeout", "cancel"}:
            await asyncio.Event().wait()
        else:
            # Service DTO validation occurs inside the transaction, before commit.
            ticket.title = ""
        return ticket

    monkeypatch.setattr(TicketRepository, "create", fail_after_flush)
    registry = ToolRegistry()
    tool = ticket_create_tool(TicketService(database))
    # Arm an immediate deadline only after the real DB flush; no slow timing assumption.
    if mode == "timeout":
        monkeypatch.setattr(asyncio, "timeout", controlled_timeout)
    registry.register(tool)
    caplog.set_level(logging.INFO)
    task = asyncio.create_task(
        ToolExecutor(registry).execute(
            ToolCall(
                id="rollback",
                name="ticket_create",
                arguments={"title": "Roll back", "description": "private-description"},
            ),
            context(a, write=True),
        )
    )
    if mode == "cancel":
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await task
        assert result.error is not None
        expected = ToolErrorCategory.TIMEOUT if mode == "timeout" else ToolErrorCategory.EXECUTION
        assert result.error.category is expected
        assert "hidden-driver" not in result.model_dump_json()
    assert calls == 1 and entered.is_set()
    async with database.transaction() as session:
        repo = TicketRepository(session)
        assert len(await repo.list(tenant_id=a)) == 2
        assert [t.id for t in await repo.list(tenant_id=b)] == [other]
    encoded = "\n".join(JsonFormatter().format(r) for r in caplog.records)
    raw = repr([r.__dict__ for r in caplog.records])
    for forbidden in ["hidden-driver", "private-description", "Authorization", "Traceback"]:
        assert forbidden not in encoded + raw


async def test_commit_failure_rolls_back(database: Database) -> None:
    a, b, _, other = await seed(database)
    calls = 0

    def fail_commit(session: object) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("private commit failure")

    event.listen(AsyncSession.sync_session_class, "before_commit", fail_commit)
    try:
        result = await ToolExecutor(build_tool_registry(database)).execute(
            ToolCall(id="commit", name="ticket_create", arguments={"title": "Fail commit"}),
            context(a, write=True),
        )
    finally:
        event.remove(AsyncSession.sync_session_class, "before_commit", fail_commit)
    assert result.error is not None and result.error.category is ToolErrorCategory.EXECUTION
    assert calls == 1
    async with database.transaction() as session:
        repo = TicketRepository(session)
        assert len(await repo.list(tenant_id=a)) == 2
        assert [t.id for t in await repo.list(tenant_id=b)] == [other]


async def test_missing_context_tenant_fk_failure_is_safe(database: Database) -> None:
    result = await ToolExecutor(build_tool_registry(database)).execute(
        ToolCall(id="fk", name="ticket_create", arguments={"title": "No tenant"}),
        context(uuid4(), write=True),
    )
    assert result.error is not None and result.error.category is ToolErrorCategory.EXECUTION
    assert result.error.message == "Tool execution failed."


async def test_system_status_uses_real_database(database: Database) -> None:
    result = await ToolExecutor(build_tool_registry(database)).execute(
        ToolCall(id="status", name="system_status", arguments={}),
        context(uuid4()),
    )
    assert result.success and result.data == {"application": "ok", "postgresql": "ok"}
