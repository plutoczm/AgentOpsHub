import asyncio
import inspect
import json
import logging
from dataclasses import FrozenInstanceError
from typing import cast
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.graph.state import CompiledStateGraph
from langsmith import Client
from pydantic import BaseModel, ValidationError

from app.agents import AgentLimits, AgentRunContext, AgentRunRequest, AgentRuntime
from app.agents.errors import (
    AgentBudgetExceededError,
    AgentConfigurationError,
    AgentDeadlineExceededError,
    AgentModelError,
    AgentProtocolError,
    AgentRuntimeError,
)
from app.agents.models import AgentState, AgentStopReason
from app.agents.runtime import tool_message
from app.core.config import Settings
from app.db.session import Database
from app.llm.errors import GatewayExhaustedError
from app.llm.models import LLMRequest, Message, Role, ToolCall
from app.main import create_app
from app.observability.logging import JsonFormatter
from app.tools import Tool, ToolEffect, ToolExecutionContext, ToolExecutionPolicy, ToolRegistry
from app.tools.builtin import build_tool_registry
from app.tools.models import ToolModel, ToolResult
from tests.agents.helpers import (
    ScriptedGateway,
    answer,
    call,
    calls,
    context,
    request,
    response,
    runtime,
)
from tests.llm.helpers import completion, gateway_for

pytestmark = pytest.mark.anyio


class ProbeInput(ToolModel):
    value: int = 0


class ProbeOutput(BaseModel):
    value: int


def probe_registry(
    seen: list[tuple[int, ToolExecutionContext]], *, write: bool = False
) -> ToolRegistry:
    async def handle(value: ProbeInput, trusted: ToolExecutionContext) -> ProbeOutput:
        seen.append((value.value, trusted))
        await asyncio.sleep(0)
        return ProbeOutput(value=value.value)

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="probe",
            description="Test probe",
            input_model=ProbeInput,
            output_model=ProbeOutput,
            effect=ToolEffect.WRITE if write else ToolEffect.READ_ONLY,
            handler=handle,
        )
    )
    return registry


async def test_compile_context_lifecycle_and_no_tool_completion() -> None:
    gateway = ScriptedGateway([answer()])
    runner = runtime(gateway)
    graph = runner._graph
    assert isinstance(graph, CompiledStateGraph)
    assert graph.context_schema is AgentRunContext
    assert graph.checkpointer is None and graph.store is None
    for node in graph.builder.nodes.values():
        assert node.retry_policy is None
        assert node.timeout is None
        assert node.trace_policy is None
    trusted = context()
    result = await runner.run(request(), trusted)
    assert result.final_message == answer()
    assert result.messages[-1] == answer()
    assert len(result.messages) == 3
    assert result.model_turn_count == 1
    assert result.tool_calls_seen == result.tool_executions == 0
    assert result.successful_tool_count == result.failed_tool_count == 0
    assert result.stop_reason is AgentStopReason.COMPLETED and result.duration_ms >= 0
    assert gateway.requests[0].route == "test-route"
    assert gateway.requests[0].request_id == trusted.request_id
    assert runner._graph is graph
    assert "config_schema" not in inspect.getsource(AgentRuntime)


async def test_multiple_rounds_order_messages_definitions_and_trusted_context() -> None:
    seen: list[tuple[int, ToolExecutionContext]] = []
    registry = probe_registry(seen)
    gateway = ScriptedGateway(
        [
            calls(call("a", value=1), call("b", value=2)),
            calls(call("c", value=3)),
            answer(),
        ]
    )
    trusted = context()
    result = await runtime(gateway, registry).run(request(), trusted)
    assert [v for v, _ in seen] == [1, 2, 3]
    assert all(
        c.tenant_id == trusted.tenant_id and c.request_id == trusted.request_id for _, c in seen
    )
    assert all(not c.policy.allow_writes for _, c in seen)
    assert gateway.requests[0].tools == registry.llm_definitions()
    assert len(result.messages) == 8
    assert [m.tool_call_id for m in result.messages if m.role is Role.TOOL] == ["a", "b", "c"]
    assert gateway.requests[1].messages[-1].tool_call_id == "b"
    assert gateway.requests[2].messages[-1].tool_call_id == "c"
    assert [len(r.messages) for r in gateway.requests] == [2, 5, 7]
    assert result.model_turn_count == result.tool_calls_seen == result.tool_executions == 3
    assert result.successful_tool_count == 3 and result.failed_tool_count == 0
    assert "tenant_id" not in str(AgentState.__annotations__)


@pytest.mark.parametrize("write", [False, True])
async def test_duplicate_prior_id_is_never_dispatched_again(write: bool) -> None:
    seen: list[tuple[int, ToolExecutionContext]] = []
    gateway = ScriptedGateway([calls(call("a")), calls(call("a")), answer()])
    result = await runtime(gateway, probe_registry(seen, write=write)).run(
        request(),
        AgentRunContext(tenant_id=uuid4(), tool_policy=ToolExecutionPolicy(allow_writes=True)),
    )
    assert len(seen) == result.tool_executions == result.successful_tool_count == 1
    assert result.tool_calls_seen == 2 and result.failed_tool_count == 0
    duplicate = gateway.requests[2].messages[-1]
    assert duplicate.tool_call_id == "a"
    assert json.loads(duplicate.content or "")["error"]["category"] == "duplicate_tool_call"


@pytest.mark.parametrize("write", [False, True])
async def test_same_batch_duplicate_rejects_before_any_execution(write: bool) -> None:
    seen: list[tuple[int, ToolExecutionContext]] = []
    gateway = ScriptedGateway([calls(call("a"), call("a"))])
    with pytest.raises(AgentProtocolError):
        await runtime(gateway, probe_registry(seen, write=write)).run(
            request(),
            AgentRunContext(tenant_id=uuid4(), tool_policy=ToolExecutionPolicy(allow_writes=True)),
        )
    assert seen == [] and len(gateway.requests) == 1


@pytest.mark.parametrize("budget", [0, 1, 2])
async def test_oversized_batch_executes_zero_calls(budget: int) -> None:
    seen: list[tuple[int, ToolExecutionContext]] = []
    gateway = ScriptedGateway([calls(call("a"), call("b"), call("c"))])
    with pytest.raises(AgentBudgetExceededError):
        await runtime(gateway, probe_registry(seen), AgentLimits(max_tool_calls=budget)).run(
            request(), context()
        )
    assert seen == [] and len(gateway.requests) == 1


async def test_tool_budget_is_cumulative_and_counts_replays() -> None:
    seen: list[tuple[int, ToolExecutionContext]] = []
    gateway = ScriptedGateway([calls(call("a")), calls(call("a"), call("b"))])
    with pytest.raises(AgentBudgetExceededError):
        await runtime(gateway, probe_registry(seen), AgentLimits(max_tool_calls=2)).run(
            request(), context()
        )
    assert len(seen) == 1 and len(gateway.requests) == 2


@pytest.mark.parametrize("turns", [1, 2, 8, 15])
async def test_semantic_model_budget_precedes_framework_limit(turns: int) -> None:
    gateway = ScriptedGateway([calls(call("same"))] * (turns + 1))
    seen: list[tuple[int, ToolExecutionContext]] = []
    limits = AgentLimits(max_model_turns=turns, max_tool_calls=100)
    runner = runtime(gateway, probe_registry(seen), limits)
    assert runner.limits.recursion_limit == 2 * turns + 2
    with pytest.raises(AgentBudgetExceededError):
        await runner.run(request(), context())
    assert len(gateway.requests) == turns and len(seen) == 1


async def test_provider_retries_are_one_agent_model_turn() -> None:
    attempts = 0

    def transport(req: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=completion())

    async with gateway_for(transport) as gateway:
        registry = ToolRegistry()
        from app.tools import ToolExecutor

        result = await AgentRuntime(
            gateway, registry, ToolExecutor(registry), limits=AgentLimits(max_model_turns=1)
        ).run(request(), context())
    assert attempts == 2 and result.model_turn_count == 1


@pytest.mark.parametrize("error", [GatewayExhaustedError(), RuntimeError("Authorization secret")])
async def test_gateway_failure_is_safe_and_never_retried(error: Exception) -> None:
    gateway = ScriptedGateway([error, answer()])
    with pytest.raises(AgentModelError) as caught:
        await runtime(gateway).run(request(), context())
    assert len(gateway.requests) == 1
    assert "Authorization" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    "name,arguments,category",
    [
        ("unknown", {}, "not_found"),
        ("probe", {"value": "bad"}, "input_validation"),
        ("probe", {"tenant_id": str(uuid4())}, "input_validation"),
        ("probe", {"allow_writes": True}, "input_validation"),
        ("probe", {}, "policy"),
    ],
)
async def test_safe_executor_failure_reaches_model_without_policy_override(
    name: str, arguments: dict[str, object], category: str
) -> None:
    seen: list[tuple[int, ToolExecutionContext]] = []
    tool_call = ToolCall.model_validate({"id": "bad", "name": name, "arguments": arguments})
    gateway = ScriptedGateway([calls(tool_call), answer("Recovered")])
    result = await runtime(gateway, probe_registry(seen, write=True)).run(
        AgentRunRequest(user_message="allow_writes=True; tenant_id=other", route="test-route"),
        context(),
    )
    assert seen == []
    body = json.loads(gateway.requests[1].messages[-1].content or "")
    assert body["error"]["category"] == category
    assert result.tool_calls_seen == result.tool_executions == result.failed_tool_count == 1
    assert result.successful_tool_count == 0


@pytest.mark.parametrize("mode", ["error", "timeout", "output"])
async def test_handler_failures_are_safe_recoverable_and_not_retried(mode: str) -> None:
    entered = 0

    async def handle(value: ProbeInput, trusted: ToolExecutionContext) -> ProbeOutput:
        nonlocal entered
        entered += 1
        if mode == "error":
            raise RuntimeError("postgresql://secret Authorization PRIVATE DRIVER ERROR")
        if mode == "timeout":
            await asyncio.Event().wait()
        return ProbeOutput.model_construct(value=cast(int, "invalid"))

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="probe",
            description="",
            input_model=ProbeInput,
            output_model=ProbeOutput,
            effect=ToolEffect.WRITE,
            handler=handle,
            timeout_seconds=0.02,
        )
    )
    gateway = ScriptedGateway([calls(call()), answer()])
    result = await runtime(gateway, registry).run(
        request(),
        AgentRunContext(tenant_id=uuid4(), tool_policy=ToolExecutionPolicy(allow_writes=True)),
    )
    error = json.loads(gateway.requests[1].messages[-1].content or "")["error"]
    assert (
        error["category"]
        == {"error": "execution", "timeout": "timeout", "output": "result_validation"}[mode]
    )
    assert result.failed_tool_count == 1 and entered == 1
    assert all(
        s not in str(gateway.requests[1].model_dump())
        for s in ["Authorization", "PRIVATE DRIVER ERROR", "postgresql://"]
    )


@pytest.mark.parametrize("location", ["model", "tool"])
@pytest.mark.parametrize("termination", ["cancel", "deadline"])
async def test_cancellation_and_deadline_stop_all_subsequent_work(
    location: str,
    termination: str,
) -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def block() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def model(req: LLMRequest) -> Message:
        await block()
        return answer()

    async def handle(value: ProbeInput, trusted: ToolExecutionContext) -> ProbeOutput:
        await block()
        return ProbeOutput(value=1)

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="probe",
            description="",
            input_model=ProbeInput,
            output_model=ProbeOutput,
            effect=ToolEffect.READ_ONLY,
            handler=handle,
        )
    )
    gateway = ScriptedGateway(
        [model if location == "model" else calls(call("a"), call("b")), answer()]
    )
    runner = runtime(
        gateway,
        registry,
        AgentLimits(total_timeout_seconds=0.15 if termination == "deadline" else 10),
    )
    task = asyncio.create_task(runner.run(request(), context()))
    await asyncio.wait_for(entered.wait(), 2)
    if termination == "cancel":
        task.cancel()
    with pytest.raises(
        asyncio.CancelledError if termination == "cancel" else AgentDeadlineExceededError
    ):
        await asyncio.wait_for(task, 2)
    assert cancelled.is_set() and len(gateway.requests) == 1


async def test_overall_deadline_is_not_reset_across_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    # Control only the outer timeout instance, advancing it during the second model turn.
    original = asyncio.timeout
    observed: list[asyncio.Timeout] = []

    def timeout(delay: float | None) -> asyncio.Timeout:
        timer = original(delay)
        if delay == 17:
            observed.append(timer)
        return timer

    async def second(req: LLMRequest) -> Message:
        assert len(observed) == 1
        observed[0].reschedule(asyncio.get_running_loop().time())
        await asyncio.Event().wait()
        return answer()

    monkeypatch.setattr(asyncio, "timeout", timeout)
    gateway = ScriptedGateway([calls(call()), second])
    seen: list[tuple[int, ToolExecutionContext]] = []
    with pytest.raises(AgentDeadlineExceededError):
        await runtime(gateway, probe_registry(seen), AgentLimits(total_timeout_seconds=17)).run(
            request(), context()
        )
    assert len(gateway.requests) == 2 and len(seen) == len(observed) == 1


async def test_framework_recursion_is_safely_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deliberately misconfigure the circuit breaker and execute the actual compiled graph.
    # No graph mocking: recursion=1 permits one model node but prevents the tool node.
    monkeypatch.setattr(AgentLimits, "recursion_limit", property(lambda self: 1))
    gateway = ScriptedGateway([calls(call()), answer()])
    seen: list[tuple[int, ToolExecutionContext]] = []
    with pytest.raises(AgentRuntimeError, match="Agent execution failed"):
        await runtime(gateway, probe_registry(seen)).run(request(), context())
    assert len(gateway.requests) == 1 and seen == []


@pytest.mark.parametrize("failure", [TimeoutError("PRIVATE"), RuntimeError("PRIVATE")])
async def test_unexpected_orchestration_error_is_not_a_deadline(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    registry = ToolRegistry()
    runner = runtime(ScriptedGateway([answer()]), registry)

    def broken() -> tuple[object, ...]:
        raise failure

    monkeypatch.setattr(registry, "llm_definitions", broken)
    with pytest.raises(AgentRuntimeError):
        await runner.run(request(), context())


@pytest.mark.parametrize("finish", ["length", "content_filter", "other"])
async def test_incomplete_model_response_is_not_success(finish: str) -> None:
    item = response(answer()).model_copy(update={"finish_reason": finish})
    with pytest.raises(AgentProtocolError):
        await runtime(ScriptedGateway([item])).run(request(), context())


async def test_nonassistant_response_rejected() -> None:
    with pytest.raises(AgentProtocolError):
        await runtime(ScriptedGateway([Message(role=Role.USER, content="wrong")])).run(
            request(), context()
        )


async def test_no_run_state_leaks_between_concurrent_tenants() -> None:
    seen: list[tuple[int, ToolExecutionContext]] = []

    async def model(req: LLMRequest) -> Message:
        await asyncio.sleep(0)
        return calls(call("shared-id")) if len(req.messages) == 2 else answer()

    gateway = ScriptedGateway([model] * 4)
    runner = runtime(gateway, probe_registry(seen))
    a, b = context(), context()
    results = await asyncio.gather(runner.run(request(), a), runner.run(request(), b))
    assert {ctx.tenant_id for _, ctx in seen} == {a.tenant_id, b.tenant_id}
    assert all(r.tool_executions == 1 and r.model_turn_count == 2 for r in results)


async def test_logs_contain_metadata_only_and_monotonic_duration(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    times = iter([10.0, 10.125])
    monkeypatch.setattr("app.agents.runtime.perf_counter", lambda: next(times))
    seen: list[tuple[int, ToolExecutionContext]] = []
    gateway = ScriptedGateway([calls(call(value=975312)), answer("PRIVATE ASSISTANT BODY")])
    result = await runtime(gateway, probe_registry(seen)).run(request(), context())
    assert result.duration_ms == 125
    raw = repr([r.__dict__ for r in caplog.records])
    formatted = "\n".join(JsonFormatter().format(r) for r in caplog.records)
    for secret in [
        "PRIVATE USER PROMPT",
        "PRIVATE ASSISTANT BODY",
        "975312",
        "arguments",
    ]:
        assert secret not in raw + formatted
    agent_records = [r for r in caplog.records if r.name == "app.agents.runtime"]
    assert [r.msg for r in agent_records] == [
        "agent_start",
        "agent_model_turn",
        "agent_tool_round",
        "agent_model_turn",
        "agent_finish",
    ]
    finish = json.loads(JsonFormatter().format(agent_records[-1]))
    assert finish["agent_model_turns"] == 2 and finish["agent_tool_executions"] == 1
    assert all(r.exc_info is None for r in agent_records)


async def test_tracing_disabled_even_with_inherited_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    from langsmith import get_tracing_context

    async def model(req: LLMRequest) -> Message:
        assert get_tracing_context()["enabled"] is False
        return answer()

    def no_send(*args: object, **kwargs: object) -> None:
        pytest.fail("External tracing attempted")

    monkeypatch.setattr(Client, "create_run", no_send)
    monkeypatch.setattr(Client, "update_run", no_send)
    await runtime(ScriptedGateway([model])).run(request(), context())


async def test_builtin_system_status_through_actual_graph() -> None:
    database = Database(Settings(_env_file=None))
    try:
        gateway = ScriptedGateway([calls(call(name="system_status")), answer()])
        result = await runtime(gateway, build_tool_registry(database)).run(request(), context())
        assert result.successful_tool_count == 1
        assert json.loads(gateway.requests[1].messages[-1].content or "")["data"] == {
            "application": "ok",
            "postgresql": "unavailable",
        }
    finally:
        await database.close()


def test_lifespan_zero_provider_internal_only() -> None:
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        runner = app.state.agent_runtime
        assert isinstance(runner, AgentRuntime)
        assert runner._gateway is app.state.llm_gateway
        assert runner._executor is app.state.tool_executor
        assert runner._registry is app.state.tool_registry
        assert set(app.openapi()["paths"]) == {"/health", "/ready"}
        for url in ["/agent", "/chat", "/agent/run", "/tools"]:
            assert client.post(url, json={}).status_code == 404
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", str(uuid4())),
        ("allow_writes", True),
        ("max_model_turns", 1000),
    ],
)
def test_request_rejects_trusted_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AgentRunRequest.model_validate({"user_message": "x", "route": "r", field: value})


@pytest.mark.parametrize(
    "values",
    [
        {"max_model_turns": 0},
        {"max_model_turns": True},
        {"max_tool_calls": -1},
        {"total_timeout_seconds": 0},
        {"total_timeout_seconds": float("inf")},
    ],
)
def test_limits_validate_before_execution(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AgentLimits.model_validate(values)


def test_trusted_context_is_frozen_and_validated() -> None:
    trusted = context()
    with pytest.raises(FrozenInstanceError):
        field_name = "tenant_id"
        setattr(trusted, field_name, uuid4())
    with pytest.raises(AgentConfigurationError):
        AgentRunContext(tenant_id=cast(UUID, "bad"))
    assert trusted.tool_policy.allow_writes is False


def test_runtime_requires_matching_registry_and_valid_limits() -> None:
    from app.tools import ToolExecutor

    with pytest.raises(AgentConfigurationError):
        AgentRuntime(ScriptedGateway([]), ToolRegistry(), ToolExecutor(ToolRegistry()))
    with pytest.raises(AgentConfigurationError):
        runtime(ScriptedGateway([]), limits=AgentLimits.model_construct(max_model_turns=0))


def test_bridge_rejects_mismatched_result_mapping() -> None:
    with pytest.raises(AgentProtocolError):
        tool_message(
            ToolResult(
                tool_call_id="other", tool_name="probe", success=True, data={}, duration_ms=0
            ),
            call(),
        )


@pytest.mark.parametrize("call_id", ["", "   "])
async def test_unusable_call_id_rejects_entire_batch(call_id: str) -> None:
    # Gateway owns general protocol validation; this tests the Agent-specific ID preflight
    # defensively at the normalized interface without replacing LangGraph.
    invalid = ToolCall.model_construct(id=call_id, name="probe", arguments={})
    seen: list[tuple[int, ToolExecutionContext]] = []
    gateway = ScriptedGateway([calls(call("valid"), invalid)])
    with pytest.raises(AgentProtocolError):
        await runtime(gateway, probe_registry(seen)).run(request(), context())
    assert seen == []


async def test_mixed_replay_and_new_ids_keep_protocol_order() -> None:
    seen: list[tuple[int, ToolExecutionContext]] = []
    gateway = ScriptedGateway(
        [
            calls(call("a", value=1)),
            calls(call("a", value=99), call("b", value=2)),
            answer(),
        ]
    )
    result = await runtime(gateway, probe_registry(seen)).run(request(), context())
    assert [v for v, _ in seen] == [1, 2]
    assert [m.tool_call_id for m in gateway.requests[-1].messages if m.role is Role.TOOL] == [
        "a",
        "a",
        "b",
    ]
    assert result.tool_calls_seen == 3 and result.tool_executions == 2


async def test_safe_tool_failure_can_be_corrected_on_next_turn() -> None:
    seen: list[tuple[int, ToolExecutionContext]] = []
    invalid = ToolCall(id="bad", name="probe", arguments={"value": "invalid"})
    gateway = ScriptedGateway([calls(invalid), calls(call("fixed", value=2)), answer()])
    result = await runtime(gateway, probe_registry(seen)).run(request(), context())
    assert [v for v, _ in seen] == [2]
    assert result.failed_tool_count == result.successful_tool_count == 1
    assert result.tool_calls_seen == result.tool_executions == 2
    assert result.model_turn_count == 3
