import json
import logging
from uuid import UUID

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import AgentLimits, AgentRunContext
from app.agents.errors import AgentBudgetExceededError, AgentModelError, AgentProtocolError
from app.db.session import Database
from app.llm.models import JsonObject, ToolCall
from app.observability.logging import JsonFormatter
from app.repositories.ticket import TicketRepository
from app.tools import ToolExecutionPolicy
from app.tools.builtin import build_tool_registry
from tests.agents.helpers import ScriptedGateway, answer, call, calls, request, runtime
from tests.integration.test_tools import seed

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def trusted(tenant_id: UUID, *, write: bool = False) -> AgentRunContext:
    return AgentRunContext(tenant_id=tenant_id, tool_policy=ToolExecutionPolicy(allow_writes=write))


async def verify_rows(database: Database, a: UUID, b: UUID, other: UUID, count: int) -> None:
    async with database.transaction() as session:
        repo = TicketRepository(session)
        assert len(await repo.list(tenant_id=a)) == count
        rows = await repo.list(tenant_id=b)
        assert len(rows) == 1 and rows[0].id == other
        assert rows[0].description == "B confidential"


@pytest.mark.parametrize("mode", ["list", "own_id", "foreign_id"])
async def test_agent_search_full_path_preserves_tenant_isolation(
    database: Database,
    mode: str,
) -> None:
    a, b, own, other = await seed(database)
    arguments: JsonObject = (
        {} if mode == "list" else {"ticket_id": str(own if mode == "own_id" else other)}
    )
    gateway = ScriptedGateway(
        [
            calls(ToolCall(id="search", name="ticket_search", arguments=arguments)),
            answer(),
        ]
    )
    result = await runtime(gateway, build_tool_registry(database)).run(request(), trusted(a))
    body = json.loads(gateway.requests[1].messages[-1].content or "")["data"]
    expected_titles = {
        "list": {"A open", "A closed"},
        "own_id": {"A open"},
        "foreign_id": set(),
    }[mode]
    assert {row["title"] for row in body["tickets"]} == expected_titles
    assert all(row["id"] != str(other) for row in body["tickets"])
    if mode == "own_id":
        assert body["tickets"][0]["id"] == str(own)
    assert "B private" not in result.model_dump_json()
    assert "B confidential" not in result.model_dump_json()
    assert result.successful_tool_count == 1
    await verify_rows(database, a, b, other, 2)


async def test_agent_create_trusted_tenant_and_private_logs(
    database: Database,
    caplog: pytest.LogCaptureFixture,
) -> None:
    a, b, _, other = await seed(database)
    caplog.set_level(logging.INFO)
    gateway = ScriptedGateway(
        [
            calls(
                call(
                    "create",
                    "ticket_create",
                    title="PRIVATE TITLE",
                    description="PRIVATE DESCRIPTION",
                )
            ),
            answer("PRIVATE ASSISTANT"),
        ]
    )
    result = await runtime(gateway, build_tool_registry(database)).run(
        request(), trusted(a, write=True)
    )
    data = json.loads(gateway.requests[1].messages[-1].content or "")["data"]
    ticket_id = UUID(data["id"])
    async with database.transaction() as session:
        repo = TicketRepository(session)
        row = await repo.get_by_id(tenant_id=a, ticket_id=ticket_id)
        assert row is not None and row.tenant_id == a and row.description == "PRIVATE DESCRIPTION"
        assert await repo.get_by_id(tenant_id=b, ticket_id=ticket_id) is None
    assert "description" not in data and "tenant_id" not in data
    assert result.tool_executions == result.successful_tool_count == 1
    logs = repr([r.__dict__ for r in caplog.records])
    logs += "\n".join(JsonFormatter().format(r) for r in caplog.records)
    for text in [
        "PRIVATE TITLE",
        "PRIVATE DESCRIPTION",
        "PRIVATE ASSISTANT",
        "PRIVATE USER PROMPT",
    ]:
        assert text not in logs
    await verify_rows(database, a, b, other, 3)


@pytest.mark.parametrize("name", ["ticket_search", "ticket_create"])
@pytest.mark.parametrize("field", ["tenant_id", "allow_writes"])
async def test_agent_injected_context_fields_rejected_before_database(
    database: Database,
    name: str,
    field: str,
) -> None:
    a, b, _, other = await seed(database)
    arguments: dict[str, object] = {field: str(b) if field == "tenant_id" else True}
    if name == "ticket_create":
        arguments["title"] = "Hijacked"
    gateway = ScriptedGateway(
        [
            calls(ToolCall.model_validate({"id": "inject", "name": name, "arguments": arguments})),
            answer(),
        ]
    )
    result = await runtime(gateway, build_tool_registry(database)).run(
        request(), trusted(a, write=True)
    )
    error = json.loads(gateway.requests[1].messages[-1].content or "")["error"]
    assert error["category"] == "input_validation"
    assert result.failed_tool_count == 1
    await verify_rows(database, a, b, other, 2)


async def test_agent_write_denial_creates_zero_tickets(database: Database) -> None:
    a, b, _, other = await seed(database)
    gateway = ScriptedGateway([calls(call("deny", "ticket_create", title="Denied")), answer()])
    result = await runtime(gateway, build_tool_registry(database)).run(request(), trusted(a))
    assert (
        json.loads(gateway.requests[1].messages[-1].content or "")["error"]["category"] == "policy"
    )
    assert result.failed_tool_count == result.tool_executions == 1
    await verify_rows(database, a, b, other, 2)


async def test_agent_duplicate_write_id_creates_at_most_one_ticket(database: Database) -> None:
    a, b, _, other = await seed(database)
    item = calls(call("same-id", "ticket_create", title="Only once"))
    gateway = ScriptedGateway([item, item, answer()])
    result = await runtime(gateway, build_tool_registry(database)).run(
        request(), trusted(a, write=True)
    )
    assert (
        result.tool_calls_seen == 2 and result.tool_executions == result.successful_tool_count == 1
    )
    assert (
        json.loads(gateway.requests[2].messages[-1].content or "")["error"]["category"]
        == "duplicate_tool_call"
    )
    await verify_rows(database, a, b, other, 3)


@pytest.mark.parametrize("mode", ["duplicate", "budget"])
async def test_agent_whole_batch_preflight_has_zero_new_writes(
    database: Database,
    mode: str,
) -> None:
    a, b, _, other = await seed(database)
    gateway = ScriptedGateway(
        [
            calls(
                call("a", "ticket_create", title="A"),
                call("a" if mode == "duplicate" else "b", "ticket_create", title="B"),
            ),
        ]
    )
    limits = AgentLimits(max_tool_calls=1 if mode == "budget" else 16)
    with pytest.raises(AgentProtocolError if mode == "duplicate" else AgentBudgetExceededError):
        await runtime(gateway, build_tool_registry(database), limits).run(
            request(), trusted(a, write=True)
        )
    await verify_rows(database, a, b, other, 2)


async def test_agent_failed_write_rolls_back_real_flush_and_does_not_retry(
    database: Database,
) -> None:
    a, b, _, other = await seed(database)
    commit_attempts: list[bool] = []

    def fail_commit(session: object) -> None:
        commit_attempts.append(True)
        raise RuntimeError("PRIVATE DB ERROR Authorization postgresql://hidden")

    # Inject a real SQLAlchemy transaction failure, preserving every application layer.
    event.listen(AsyncSession.sync_session_class, "before_commit", fail_commit)
    gateway = ScriptedGateway(
        [
            calls(call("write", "ticket_create", title="Roll back")),
            calls(call("write", "ticket_create", title="Roll back")),
            answer(),
        ]
    )
    try:
        result = await runtime(gateway, build_tool_registry(database)).run(
            request(), trusted(a, write=True)
        )
    finally:
        event.remove(AsyncSession.sync_session_class, "before_commit", fail_commit)
    assert commit_attempts == [True]
    assert result.failed_tool_count == result.tool_executions == 1
    assert result.tool_calls_seen == 2
    assert (
        json.loads(gateway.requests[1].messages[-1].content or "")["error"]["category"]
        == "execution"
    )
    assert "PRIVATE DB ERROR" not in result.model_dump_json()
    assert "Authorization" not in result.model_dump_json()
    await verify_rows(database, a, b, other, 2)


async def test_agent_failure_after_committed_write_preserves_partial_completion(
    database: Database,
) -> None:
    a, b, _, other = await seed(database)
    gateway = ScriptedGateway(
        [
            calls(call("a", "ticket_create", title="Committed")),
            RuntimeError("lost model response"),
        ]
    )
    with pytest.raises(AgentModelError):
        await runtime(gateway, build_tool_registry(database)).run(request(), trusted(a, write=True))
    assert len(gateway.requests) == 2
    await verify_rows(database, a, b, other, 3)


async def test_different_ids_are_not_business_idempotency(database: Database) -> None:
    a, b, _, other = await seed(database)
    gateway = ScriptedGateway(
        [
            calls(
                call("a", "ticket_create", title="Same operation"),
                call("b", "ticket_create", title="Same operation"),
            ),
            answer(),
        ]
    )
    result = await runtime(gateway, build_tool_registry(database)).run(
        request(), trusted(a, write=True)
    )
    assert result.tool_executions == 2
    await verify_rows(database, a, b, other, 4)


async def test_agent_system_status_uses_real_postgresql(database: Database) -> None:
    gateway = ScriptedGateway([calls(call("status", "system_status")), answer()])
    from uuid import uuid4

    result = await runtime(gateway, build_tool_registry(database)).run(request(), trusted(uuid4()))
    assert result.successful_tool_count == 1
    assert json.loads(gateway.requests[1].messages[-1].content or "")["data"] == {
        "application": "ok",
        "postgresql": "ok",
    }
