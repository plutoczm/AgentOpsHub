from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.session import Database
from app.llm.models import ToolCall
from app.main import create_app
from app.tools import ToolEffect, ToolExecutionContext, ToolExecutionPolicy, ToolExecutor
from app.tools.builtin import build_tool_registry
from app.tools.builtin.system_status import system_status_tool
from app.tools.errors import ToolInputValidationError
from app.tools.models import ToolErrorCategory


@pytest.mark.anyio
@pytest.mark.parametrize("available", [True, False])
async def test_system_status_is_small_and_safe(available: bool) -> None:
    async def check() -> bool:
        return available

    tool = system_status_tool(check)
    # Use the injected check instead of changing production registration.
    from app.tools import ToolRegistry

    registry = ToolRegistry()
    registry.register(tool)
    result = await ToolExecutor(registry).execute(
        ToolCall(id="status", name="system_status", arguments={}),
        ToolExecutionContext(tenant_id=uuid4()),
    )
    assert tool.effect is ToolEffect.READ_ONLY and result.success
    assert result.data == {"application": "ok", "postgresql": "ok" if available else "unavailable"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "name,arguments",
    [
        ("ticket_search", {"tenant_id": str(uuid4())}),
        ("ticket_create", {"title": "x", "tenant_id": str(uuid4())}),
        ("ticket_create", {"title": "x", "id": str(uuid4())}),
        ("ticket_create", {"title": "x", "created_at": "2026-01-01"}),
        ("ticket_create", {"title": "x", "updated_at": "2026-01-01"}),
        ("ticket_create", {"title": "x", "allow_writes": True}),
        ("ticket_create", {"title": "x", "priority": "urgent"}),
        ("ticket_create", {"title": " "}),
        ("ticket_create", {"title": "x" * 301}),
        ("ticket_create", {"title": "x", "description": "x" * 10001}),
        ("ticket_create", {"description": "missing title"}),
        ("ticket_search", {"limit": 0}),
        ("ticket_search", {"limit": 101}),
        ("ticket_search", {"limit": "3"}),
        ("ticket_search", {"limit": True}),
        ("ticket_search", {"status": "unknown"}),
        ("ticket_search", {"ticket_id": "bad"}),
        ("system_status", {"url": "https://example.com"}),
    ],
)
async def test_builtin_input_rejection_before_database(
    name: str, arguments: dict[str, object]
) -> None:
    registry = build_tool_registry(Database(Settings(_env_file=None)))
    result = await ToolExecutor(registry).execute(
        ToolCall.model_validate({"id": "reject", "name": name, "arguments": arguments}),
        ToolExecutionContext(tenant_id=uuid4(), policy=ToolExecutionPolicy(allow_writes=True)),
    )
    assert result.error is not None and result.error.category is ToolErrorCategory.INPUT_VALIDATION


def test_schemas_have_no_context_and_enums_are_json_compatible() -> None:
    registry = build_tool_registry(Database(Settings(_env_file=None)))
    assert [t.name for t in registry.list_tools()] == [
        "system_status",
        "ticket_create",
        "ticket_search",
    ]
    for definition in registry.llm_definitions():
        assert definition.parameters["additionalProperties"] is False
        assert "tenant_id" not in str(definition.parameters["properties"])
        assert "allow_writes" not in str(definition.parameters["properties"])
    search = registry.lookup("ticket_search")
    assert search.validate_input({"status": "open", "ticket_id": str(uuid4())})
    assert registry.lookup("ticket_create").validate_input({"title": "x", "priority": "high"})
    with pytest.raises(ToolInputValidationError):
        search.validate_input({"tenant_id": str(uuid4())})


@pytest.mark.anyio
async def test_unconfigured_services_normalize_without_secret_output() -> None:
    runtime = ToolExecutor(build_tool_registry(Database(Settings(_env_file=None))))
    context = ToolExecutionContext(tenant_id=uuid4(), policy=ToolExecutionPolicy(allow_writes=True))
    for name, arguments in [("ticket_search", {}), ("ticket_create", {"title": "x"})]:
        result = await runtime.execute(
            ToolCall.model_validate({"id": "x", "name": name, "arguments": arguments}), context
        )
        assert result.error is not None and result.error.category is ToolErrorCategory.EXECUTION
        assert "PostgreSQL" not in result.model_dump_json()
    status = await runtime.execute(ToolCall(id="x", name="system_status", arguments={}), context)
    assert status.data == {"application": "ok", "postgresql": "unavailable"}


def test_lifespan_composes_internal_runtime_without_http_endpoint() -> None:
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        assert isinstance(app.state.tool_executor, ToolExecutor)
        assert len(app.state.tool_registry.llm_definitions()) == 3
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        assert client.post("/tools/execute", json={}).status_code == 404
        assert client.post("/tools/ticket_create", json={}).status_code == 404
        assert set(app.openapi()["paths"]) == {"/health", "/ready"}
