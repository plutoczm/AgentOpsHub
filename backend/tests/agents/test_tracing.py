import asyncio
import json
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agents import (
    AgentLimits,
    AgentModelPolicy,
    AgentRunContext,
    AgentRunRequest,
    AgentRuntime,
)
from app.agents.errors import AgentDeadlineExceededError, AgentModelError, AgentProtocolError
from app.agents.tracing import AgentTraceEventType, AgentTraceRecorder
from app.llm.errors import LLMTimeoutError
from app.llm.models import (
    Attempt,
    CostEstimate,
    DeploymentType,
    ErrorCategory,
    LLMRequest,
    LLMResponse,
    Message,
    Role,
    Usage,
)
from app.tools import ToolExecutor, ToolRegistry
from app.tools.models import ToolEffect, ToolResult
from tests.agents.helpers import ScriptedGateway, answer, call, calls, response

SECRET_QUERY_MARKER = "SECRET_QUERY_MARKER"
SECRET_EVIDENCE_MARKER = "SECRET_EVIDENCE_MARKER"
SECRET_ASSISTANT_MARKER = "SECRET_ASSISTANT_MARKER"
SECRET_ARGUMENT_MARKER = "SECRET_ARGUMENT_MARKER"
SECRET_TENANT_MARKER = "SECRET_TENANT_MARKER"
SECRET_API_KEY_MARKER = "SECRET_API_KEY_MARKER"


def _recorder(*, max_events: int = 20) -> AgentTraceRecorder:
    return AgentTraceRecorder(
        run_id=uuid4(),
        request_id=uuid4(),
        case_id="privacy-case",
        max_events=max_events,
        sensitive_values=(SECRET_API_KEY_MARKER,),
    )


def test_trace_is_allowlisted_immutable_and_contains_no_payload_markers() -> None:
    recorder = _recorder()
    attempt = Attempt(
        sequence=1,
        provider=SECRET_API_KEY_MARKER,
        model="safe-model",
        deployment_type=DeploymentType.CLOUD,
        attempt_number=1,
        outcome="success",
        latency_ms=1,
    )
    response = LLMResponse(
        message=Message(role=Role.ASSISTANT, content=SECRET_ASSISTANT_MARKER),
        finish_reason="stop",
        usage=Usage(input_tokens=12, output_tokens=4),
        provider=SECRET_API_KEY_MARKER,
        model="safe-model",
        deployment_type=DeploymentType.CLOUD,
        latency_ms=2,
        attempts=(attempt,),
        cost=CostEstimate(
            input_cost=Decimal("0.000012"),
            output_cost=Decimal("0.000008"),
            total_cost=Decimal("0.000020"),
            currency="USD",
        ),
    )
    recorder.record_response(1, response, tool_calls_proposed=1)
    recorder.record_tool_call(name=SECRET_API_KEY_MARKER, effect=ToolEffect.READ_ONLY)
    recorder.record_tool_result(
        ToolResult(
            tool_call_id="not-exported",
            tool_name="knowledge_search",
            success=True,
            data={
                "context_fingerprint": "a" * 64,
                "retriever": SECRET_API_KEY_MARKER,
                "query": SECRET_QUERY_MARKER,
                "argument": SECRET_ARGUMENT_MARKER,
                "tenant": SECRET_TENANT_MARKER,
                "evidence": [{"content": SECRET_EVIDENCE_MARKER}],
            },
            duration_ms=3,
        ),
        effect=ToolEffect.READ_ONLY,
    )
    summary = recorder.finalize(status="complete", error_category=None, model_turn_count=1)

    encoded = json.dumps(
        {
            "events": [event.model_dump(mode="json") for event in recorder.events],
            "summary": summary.model_dump(mode="json"),
        },
        sort_keys=True,
    )
    for marker in (
        SECRET_QUERY_MARKER,
        SECRET_EVIDENCE_MARKER,
        SECRET_ASSISTANT_MARKER,
        SECRET_ARGUMENT_MARKER,
        SECRET_TENANT_MARKER,
        SECRET_API_KEY_MARKER,
        "not-exported",
    ):
        assert marker not in encoded
    assert summary.observed_input_tokens == 12
    assert summary.observed_output_tokens == 4
    assert summary.observed_total_tokens == 16
    assert summary.known_estimated_cost == Decimal("0.000020")
    assert summary.usage_complete and summary.cost_complete
    assert [event.sequence for event in recorder.events] == list(range(1, 6))
    assert recorder.events[-1].event_type is AgentTraceEventType.RUN_COMPLETE
    with pytest.raises(ValidationError):
        recorder.events[0].sequence = 99  # type: ignore[misc]


def test_unknown_usage_and_cost_remain_null_and_failed_attempts_invalidate_cost() -> None:
    unknown = _recorder()
    unknown.record_response(
        1,
        LLMResponse(
            message=Message(role=Role.ASSISTANT, content="safe"),
            finish_reason="stop",
            provider="provider",
            model="model",
            deployment_type=DeploymentType.PRIVATE,
            latency_ms=1,
            attempts=(),
        ),
        tool_calls_proposed=0,
    )
    summary = unknown.finalize(status="complete", error_category=None)
    assert summary.observed_input_tokens is None
    assert summary.observed_output_tokens is None
    assert summary.observed_total_tokens is None
    assert summary.known_estimated_cost is None
    assert not summary.usage_complete and not summary.cost_complete

    retried = _recorder()
    attempts = (
        Attempt(
            sequence=1,
            provider="provider",
            model="model",
            deployment_type=DeploymentType.CLOUD,
            attempt_number=1,
            outcome="error",
            error_category=ErrorCategory.TIMEOUT,
            latency_ms=2,
            retryable=True,
        ),
        Attempt(
            sequence=2,
            provider="provider",
            model="model",
            deployment_type=DeploymentType.CLOUD,
            attempt_number=2,
            outcome="success",
            latency_ms=1,
        ),
    )
    retried.record_response(
        1,
        LLMResponse(
            message=Message(role=Role.ASSISTANT, content="safe"),
            finish_reason="stop",
            usage=Usage(input_tokens=2, output_tokens=1),
            provider="provider",
            model="model",
            deployment_type=DeploymentType.CLOUD,
            latency_ms=3,
            attempts=attempts,
            cost=CostEstimate(
                input_cost=Decimal("0"),
                output_cost=Decimal("0"),
                total_cost=Decimal("0"),
                currency="USD",
            ),
        ),
        tool_calls_proposed=0,
    )
    retry_summary = retried.finalize(status="complete", error_category=None)
    assert retry_summary.retry_count == 1
    assert retry_summary.known_estimated_cost == Decimal("0")
    assert not retry_summary.cost_complete


def test_trace_capacity_is_a_hard_limit() -> None:
    recorder = _recorder(max_events=1)
    recorder.record_tool_call(name="knowledge_search", effect=ToolEffect.READ_ONLY)
    with pytest.raises(RuntimeError, match="capacity"):
        recorder.record_tool_call(name="system_status", effect=ToolEffect.READ_ONLY)


def test_unknown_tool_names_are_not_copied_to_trace() -> None:
    recorder = _recorder()
    recorder.record_tool_call(name=SECRET_API_KEY_MARKER, effect=None)
    recorder.record_duplicate_tool_result(name=SECRET_API_KEY_MARKER, effect=None)
    recorder.finalize(status="error", error_category="protocol")
    assert all(event.tool_name == "unknown" for event in recorder.events if event.tool_name)


@pytest.mark.anyio
async def test_runtime_trace_is_per_run_and_model_policy_is_construction_time(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = ToolRegistry()
    gateway = ScriptedGateway([answer(SECRET_ASSISTANT_MARKER), answer("second")])
    runtime = AgentRuntime(
        gateway,
        registry,
        ToolExecutor(registry),
        model_policy=AgentModelPolicy(temperature=0, max_output_tokens=77),
    )
    tenant_id = uuid4()
    first_recorder = AgentTraceRecorder.for_limits(
        run_id=uuid4(),
        request_id=uuid4(),
        case_id="first",
        max_model_turns=runtime.limits.max_model_turns,
        max_tool_calls=runtime.limits.max_tool_calls,
    )
    second_recorder = AgentTraceRecorder.for_limits(
        run_id=uuid4(),
        request_id=uuid4(),
        case_id="second",
        max_model_turns=runtime.limits.max_model_turns,
        max_tool_calls=runtime.limits.max_tool_calls,
    )

    first, second = await asyncio.gather(
        runtime.run(
            AgentRunRequest(user_message=SECRET_QUERY_MARKER, route="test-route"),
            AgentRunContext(
                tenant_id=tenant_id,
                request_id=first_recorder.request_id,
            ),
            trace=first_recorder,
        ),
        runtime.run(
            AgentRunRequest(user_message="second request", route="test-route"),
            AgentRunContext(
                tenant_id=tenant_id,
                request_id=second_recorder.request_id,
            ),
            trace=second_recorder,
        ),
    )

    assert gateway.requests[0].temperature == 0
    assert gateway.requests[0].max_output_tokens == 77
    assert first.trace_summary is not None and second.trace_summary is not None
    assert first.trace_summary.case_id == "first"
    assert second.trace_summary.case_id == "second"
    assert first.trace_events[-1].event_type is AgentTraceEventType.RUN_COMPLETE
    assert second.trace_events[-1].event_type is AgentTraceEventType.RUN_COMPLETE
    assert tenant_id.hex not in json.dumps([e.model_dump(mode="json") for e in first.trace_events])
    assert SECRET_QUERY_MARKER not in json.dumps(
        [e.model_dump(mode="json") for e in first.trace_events]
    )
    assert SECRET_ASSISTANT_MARKER not in json.dumps(
        [e.model_dump(mode="json") for e in first.trace_events]
    )
    assert SECRET_QUERY_MARKER not in caplog.text
    assert SECRET_ASSISTANT_MARKER not in caplog.text


@pytest.mark.anyio
async def test_provider_timeout_is_attached_as_a_safe_run_error_trace() -> None:
    attempt = Attempt(
        sequence=1,
        provider="provider",
        model="model",
        deployment_type=DeploymentType.CLOUD,
        attempt_number=1,
        outcome="error",
        error_category=ErrorCategory.TIMEOUT,
        latency_ms=10,
        retryable=True,
    )
    gateway = ScriptedGateway([LLMTimeoutError(attempts=(attempt,))])
    registry = ToolRegistry()
    runtime = AgentRuntime(gateway, registry, ToolExecutor(registry))
    recorder = AgentTraceRecorder.for_limits(
        run_id=uuid4(),
        request_id=uuid4(),
        case_id="timeout-case",
        max_model_turns=runtime.limits.max_model_turns,
        max_tool_calls=runtime.limits.max_tool_calls,
    )
    with pytest.raises(AgentModelError) as caught:
        await runtime.run(
            AgentRunRequest(user_message="safe task", route="test-route"),
            AgentRunContext(tenant_id=uuid4(), request_id=recorder.request_id),
            trace=recorder,
        )
    assert caught.value.trace_summary is not None
    assert caught.value.trace_summary.error_category == "model"
    attempt_events = [
        event
        for event in caught.value.trace_events
        if event.event_type is AgentTraceEventType.GATEWAY_ATTEMPT
    ]
    assert len(attempt_events) == 1 and attempt_events[0].error_category == "timeout"
    assert caught.value.trace_events[-1].event_type is AgentTraceEventType.RUN_ERROR


@pytest.mark.anyio
async def test_protocol_error_has_a_terminal_safe_trace() -> None:
    invalid_finish = response(answer("safe body")).model_copy(update={"finish_reason": "length"})
    gateway = ScriptedGateway([invalid_finish])
    registry = ToolRegistry()
    runtime = AgentRuntime(gateway, registry, ToolExecutor(registry))
    recorder = AgentTraceRecorder.for_limits(
        run_id=uuid4(),
        request_id=uuid4(),
        case_id="protocol-case",
        max_model_turns=runtime.limits.max_model_turns,
        max_tool_calls=runtime.limits.max_tool_calls,
    )
    with pytest.raises(AgentProtocolError) as caught:
        await runtime.run(
            AgentRunRequest(user_message="safe task", route="test-route"),
            AgentRunContext(tenant_id=uuid4(), request_id=recorder.request_id),
            trace=recorder,
        )
    assert caught.value.trace_summary is not None
    assert caught.value.trace_summary.model_turn_count == 1
    assert caught.value.trace_events[-1].event_type is AgentTraceEventType.RUN_ERROR


@pytest.mark.anyio
async def test_agent_deadline_has_a_safe_error_trace() -> None:
    async def slow(_: LLMRequest) -> Message:
        await asyncio.sleep(0.05)
        return answer()

    gateway = ScriptedGateway([slow])
    registry = ToolRegistry()
    limits = AgentLimits(total_timeout_seconds=0.005)
    runtime = AgentRuntime(gateway, registry, ToolExecutor(registry), limits=limits)
    recorder = AgentTraceRecorder.for_limits(
        run_id=uuid4(),
        request_id=uuid4(),
        case_id="deadline-case",
        max_model_turns=limits.max_model_turns,
        max_tool_calls=limits.max_tool_calls,
    )
    with pytest.raises(AgentDeadlineExceededError) as caught:
        await runtime.run(
            AgentRunRequest(user_message="safe task", route="test-route"),
            AgentRunContext(tenant_id=uuid4(), request_id=recorder.request_id),
            trace=recorder,
        )
    assert caught.value.trace_summary is not None
    assert caught.value.trace_summary.error_category == "deadline"
    assert caught.value.trace_events[-1].event_type is AgentTraceEventType.RUN_ERROR


@pytest.mark.anyio
async def test_unknown_tool_result_is_traced_without_copying_model_tool_name() -> None:
    gateway = ScriptedGateway(
        [calls(call("missing", "SECRET_API_KEY_MARKER")), answer("Safe refusal")]
    )
    registry = ToolRegistry()
    runtime = AgentRuntime(gateway, registry, ToolExecutor(registry))
    recorder = AgentTraceRecorder.for_limits(
        run_id=uuid4(),
        request_id=uuid4(),
        case_id="tool-failure-case",
        max_model_turns=runtime.limits.max_model_turns,
        max_tool_calls=runtime.limits.max_tool_calls,
        sensitive_values=(SECRET_API_KEY_MARKER,),
    )
    result = await runtime.run(
        AgentRunRequest(user_message="safe task", route="test-route"),
        AgentRunContext(tenant_id=uuid4(), request_id=recorder.request_id),
        trace=recorder,
    )
    call_event = next(
        event for event in result.trace_events if event.event_type is AgentTraceEventType.TOOL_CALL
    )
    result_event = next(
        event
        for event in result.trace_events
        if event.event_type is AgentTraceEventType.TOOL_RESULT
    )
    assert call_event.tool_name == result_event.tool_name == "unknown"
    assert result_event.error_category == "not_found"
    assert result.trace_summary is not None and result.trace_summary.failed_tool_count == 1
