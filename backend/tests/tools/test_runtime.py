import asyncio
import json
import logging
from dataclasses import replace
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.llm.models import JsonObject, ToolCall, ToolDefinition
from app.observability.logging import JsonFormatter, request_id_context
from app.tools import (
    Tool,
    ToolEffect,
    ToolExecutionContext,
    ToolExecutionPolicy,
    ToolExecutor,
    ToolModel,
    ToolRegistry,
    ToolResult,
)
from app.tools.errors import ToolNotFoundError, ToolPolicyError
from app.tools.models import ToolErrorCategory, ToolFailure


class EchoInput(ToolModel):
    text: str
    count: int = 1


class EchoOutput(ToolModel):
    text: str


async def echo(arguments: EchoInput, context: ToolExecutionContext) -> EchoOutput:
    return EchoOutput(text=arguments.text * arguments.count)


def echo_tool() -> Tool[EchoInput, EchoOutput]:
    return Tool(
        name="echo",
        description="Repeat text",
        input_model=EchoInput,
        output_model=EchoOutput,
        effect=ToolEffect.READ_ONLY,
        handler=echo,
    )


def executor(tool: Tool[EchoInput, EchoOutput]) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(registry)


def test_registry_order_duplicates_lookup_and_schema() -> None:
    registry = ToolRegistry()
    assert registry.list_tools() == ()
    assert registry.llm_definitions() == ()
    original = echo_tool()
    registry.register(replace(original, name="z_echo"))
    registry.register(original)
    assert registry.lookup("echo") is original
    assert [t.name for t in registry.list_tools()] == ["echo", "z_echo"]
    with pytest.raises(ValueError, match="already registered"):
        registry.register(replace(original, description="replacement"))
    assert registry.lookup("echo") is original
    with pytest.raises(ToolNotFoundError):
        registry.lookup("absent")
    definitions = registry.llm_definitions()
    assert all(isinstance(item, ToolDefinition) for item in definitions)
    assert definitions[0].parameters == EchoInput.model_json_schema()
    assert definitions[0].parameters["additionalProperties"] is False
    assert definitions[0].parameters["required"] == ["text"]
    definitions[0].parameters.clear()
    assert registry.llm_definitions()[0].parameters["type"] == "object"


@pytest.mark.parametrize(
    "name", ["", "Upper", "a-b", "a.b", "a b", "1first", "_first", "a" * 65, "a\n"]
)
def test_invalid_names(name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        replace(echo_tool(), name=name)


def test_valid_name_boundary() -> None:
    assert replace(echo_tool(), name="a" + "0_" * 31 + "z").name.endswith("z")


@pytest.mark.parametrize("timeout", [0.0, -1.0, 301.0, float("inf"), float("nan")])
def test_invalid_timeouts(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        replace(echo_tool(), timeout_seconds=timeout)


def test_effect_must_be_typed() -> None:
    with pytest.raises(ValueError, match="effect"):
        replace(echo_tool(), effect=cast(ToolEffect, "read_only"))


@pytest.mark.anyio
async def test_success_preserves_context_and_normalizes_result() -> None:
    context = ToolExecutionContext(tenant_id=uuid4(), request_id=uuid4())
    received: list[ToolExecutionContext] = []

    async def handler(arguments: EchoInput, trusted: ToolExecutionContext) -> EchoOutput:
        received.append(trusted)
        return await echo(arguments, trusted)

    result = await executor(replace(echo_tool(), handler=handler)).execute(
        ToolCall(id="call_123", name="echo", arguments={"text": "hello", "count": 2}),
        context,
    )
    assert isinstance(result, ToolResult)
    assert result.success and result.data == {"text": "hellohello"} and result.error is None
    assert result.tool_call_id == "call_123" and result.tool_name == "echo"
    assert result.duration_ms >= 0
    assert received == [context] and received[0].tenant_id == context.tenant_id
    assert "hello" not in repr(result)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "arguments",
    [
        None,
        [],
        "{bad json",
        {},
        {"text": 7},
        {"text": "x", "count": "2"},
        {"text": "x", "count": True},
        {"text": "x", "extra": "no"},
        {"text": "x", "count": float("nan")},
        {"text": "x", "count": object()},
    ],
)
async def test_invalid_arguments_never_reach_handler(arguments: object) -> None:
    calls = 0

    async def handler(arguments: EchoInput, context: ToolExecutionContext) -> EchoOutput:
        nonlocal calls
        calls += 1
        return await echo(arguments, context)

    # Deliberately bypass the Phase 2 object check to exercise runtime defense in depth.
    call = ToolCall.model_construct(id="bad", name="echo", arguments=cast(JsonObject, arguments))
    result = await executor(replace(echo_tool(), handler=handler)).execute(
        call,
        ToolExecutionContext(tenant_id=uuid4()),
    )
    assert not result.success and result.error is not None
    assert result.error.category is ToolErrorCategory.INPUT_VALIDATION
    assert calls == 0


@pytest.mark.parametrize("arguments", [[], None, "{bad", 1])
def test_protocol_rejects_nonobject_arguments(arguments: object) -> None:
    with pytest.raises(ValidationError):
        ToolCall.model_validate({"id": "x", "name": "echo", "arguments": arguments})


@pytest.mark.anyio
@pytest.mark.parametrize("allowed", [False, True])
async def test_write_policy_precedes_handler(allowed: bool) -> None:
    calls = 0

    async def handler(arguments: EchoInput, context: ToolExecutionContext) -> EchoOutput:
        nonlocal calls
        calls += 1
        return await echo(arguments, context)

    result = await executor(replace(echo_tool(), effect=ToolEffect.WRITE, handler=handler)).execute(
        ToolCall(id="write", name="echo", arguments={"text": "x"}),
        ToolExecutionContext(tenant_id=uuid4(), policy=ToolExecutionPolicy(allow_writes=allowed)),
    )
    assert result.success is allowed and calls == int(allowed)
    if not allowed:
        assert result.error is not None and result.error.category is ToolErrorCategory.POLICY


def test_context_requires_typed_tenant_and_boolean_policy() -> None:
    for data in [{}, {"tenant_id": None}, {"tenant_id": "bad"}, {"tenant_id": str(uuid4())}]:
        with pytest.raises(ValidationError):
            ToolExecutionContext.model_validate(data)
    with pytest.raises(ValidationError):
        ToolExecutionPolicy.model_validate({"allow_writes": "false"})
    context = ToolExecutionContext(tenant_id=uuid4())
    assert context.policy.allow_writes is False
    with pytest.raises(ValidationError):
        context.tenant_id = uuid4()  # type: ignore[misc]


@pytest.mark.anyio
async def test_invalid_constructed_context_rejected_before_handler() -> None:
    context = ToolExecutionContext.model_construct(tenant_id=cast(UUID, None))
    with pytest.raises(ValidationError):
        await executor(echo_tool()).execute(
            ToolCall(id="x", name="echo", arguments={"text": "x"}),
            context,
        )


@pytest.mark.anyio
async def test_timeout_and_cancellation_release_handler_without_retry() -> None:
    started, cleaned = asyncio.Event(), asyncio.Event()
    calls = 0

    async def hanging(arguments: EchoInput, context: ToolExecutionContext) -> EchoOutput:
        nonlocal calls
        calls += 1
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
        return EchoOutput(text="unreachable")

    tool = replace(echo_tool(), handler=hanging, effect=ToolEffect.WRITE, timeout_seconds=0.01)
    context = ToolExecutionContext(tenant_id=uuid4(), policy=ToolExecutionPolicy(allow_writes=True))
    call = ToolCall(id="timeout", name="echo", arguments={"text": "private"})
    result = await executor(tool).execute(call, context)
    assert result.error is not None and result.error.category is ToolErrorCategory.TIMEOUT
    assert calls == 1 and cleaned.is_set()
    started.clear()
    cleaned.clear()
    task = asyncio.create_task(executor(replace(tool, timeout_seconds=10)).execute(call, context))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == 2 and cleaned.is_set()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [RuntimeError, TimeoutError, ToolPolicyError])
async def test_internal_errors_are_safe_and_never_retried(failure: type[Exception]) -> None:
    calls = 0

    async def fail(arguments: EchoInput, context: ToolExecutionContext) -> EchoOutput:
        nonlocal calls
        calls += 1
        exc = failure()
        exc.args = ("postgresql://private-path Authorization: Bearer sensitive-payload",)
        raise exc

    result = await executor(replace(echo_tool(), handler=fail, effect=ToolEffect.WRITE)).execute(
        ToolCall(id="x", name="echo", arguments={"text": "private-arguments"}),
        ToolExecutionContext(tenant_id=uuid4(), policy=ToolExecutionPolicy(allow_writes=True)),
    )
    assert result.error is not None
    expected = (
        ToolErrorCategory.POLICY if failure is ToolPolicyError else ToolErrorCategory.EXECUTION
    )
    assert result.error.category is expected and calls == 1
    assert result.data is None
    for forbidden in [
        "postgresql",
        "Authorization",
        "sensitive",
        "private-",
        "Traceback",
        __file__,
    ]:
        assert forbidden not in result.model_dump_json()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "output", [EchoOutput.model_construct(text=cast(str, 123)), EchoInput(text="x"), {}]
)
async def test_invalid_output_is_classified(output: object) -> None:
    async def invalid(arguments: EchoInput, context: ToolExecutionContext) -> EchoOutput:
        return cast(EchoOutput, output)

    result = await executor(replace(echo_tool(), handler=invalid)).execute(
        ToolCall(id="x", name="echo", arguments={"text": "x"}),
        ToolExecutionContext(tenant_id=uuid4()),
    )
    assert result.error is not None and result.error.category is ToolErrorCategory.RESULT_VALIDATION


@pytest.mark.anyio
async def test_unknown_name_and_payloads_are_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.tools.executor")
    context = ToolExecutionContext(tenant_id=uuid4(), request_id=uuid4())
    runtime = executor(echo_tool())
    await runtime.execute(
        ToolCall(id="secret_call", name="echo", arguments={"text": "private body"}), context
    )
    await runtime.execute(ToolCall(id="x", name="echo", arguments={"text": 1}), context)
    missing = await runtime.execute(ToolCall(id="x", name="secret_unknown", arguments={}), context)
    assert missing.error is not None and missing.error.category is ToolErrorCategory.NOT_FOUND
    records = [r for r in caplog.records if r.name == "app.tools.executor"]
    encoded = "\n".join(JsonFormatter().format(r) for r in records)
    raw = repr([r.__dict__ for r in records])
    for forbidden in ["private body", "secret_call", "secret_unknown", str(context.tenant_id)]:
        assert forbidden not in raw + encoded
    events = [json.loads(JsonFormatter().format(r)) for r in records]
    assert [e["event"] for e in events] == [
        "tool_start",
        "tool_result",
        "tool_start",
        "tool_error",
        "tool_error",
    ]
    assert all(e["request_id"] == str(context.request_id) for e in events)
    assert events[1]["tool_effect"] == "read_only" and events[1]["duration_ms"] >= 0


@pytest.mark.anyio
async def test_inherited_request_context_is_preserved(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.tools.executor")
    token = request_id_context.set(str(uuid4()))
    try:
        await executor(echo_tool()).execute(
            ToolCall(id="x", name="echo", arguments={"text": "x"}),
            ToolExecutionContext(tenant_id=uuid4()),
        )
        assert all(
            json.loads(JsonFormatter().format(r))["request_id"] == request_id_context.get()
            for r in caplog.records
        )
    finally:
        request_id_context.reset(token)


@pytest.mark.parametrize(
    "success,data,error",
    [
        (True, None, None),
        (True, {}, ToolFailure(category=ToolErrorCategory.EXECUTION, message="Failed")),
        (False, {}, ToolFailure(category=ToolErrorCategory.EXECUTION, message="Failed")),
        (False, None, None),
    ],
)
def test_result_invariants(
    success: bool, data: JsonObject | None, error: ToolFailure | None
) -> None:
    with pytest.raises(ValidationError):
        ToolResult(
            tool_call_id="x",
            tool_name="echo",
            success=success,
            data=data,
            error=error,
            duration_ms=0,
        )
