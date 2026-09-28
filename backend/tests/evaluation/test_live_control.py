from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import pytest
from tests.agents.helpers import answer, call, calls, context, response
from tests.agents.helpers import request as agent_request
from tests.llm.helpers import request as llm_request

from app.agents import AgentLimits, AgentModelPolicy, AgentRuntime
from app.agents.errors import AgentModelError
from app.agents.tracing import AgentTraceEvent
from app.evaluation.live_control import (
    LiveBatchControl,
    LiveBatchStoppedError,
    ObservedCostGateway,
    run_controlled_cases,
)
from app.llm.errors import (
    AuthenticationError,
    BadRequestError,
    ConfigurationError,
    GatewayExhaustedError,
    LLMError,
    LLMTimeoutError,
    ProviderResponseError,
    ProviderUnavailableError,
    RateLimitError,
    UnsupportedCapabilityError,
)
from app.llm.models import (
    Attempt,
    CostEstimate,
    DeploymentType,
    ErrorCategory,
    LLMRequest,
    LLMResponse,
    Message,
    Usage,
)
from app.tools import ToolExecutor, ToolRegistry
from app.tools.builtin.system_status import system_status_tool

pytestmark = pytest.mark.anyio


def _attempt(
    number: int,
    *,
    outcome: Literal["success", "error", "cancelled"] = "success",
    category: ErrorCategory | None = None,
    retryable: bool = False,
    status: int | None = None,
) -> Attempt:
    return Attempt(
        sequence=number,
        provider="deepseek",
        model="deepseek-flash",
        deployment_type=DeploymentType.CLOUD,
        attempt_number=number,
        outcome=outcome,
        error_category=category,
        http_status=status,
        latency_ms=1,
        retryable=retryable,
    )


def _cost(value: str, currency: str = "USD") -> CostEstimate:
    amount = Decimal(value)
    return CostEstimate(
        input_cost=amount,
        output_cost=Decimal("0"),
        total_cost=amount,
        currency=currency,
    )


def _response(
    cost: CostEstimate | None,
    *,
    message: Message | None = None,
    attempts: tuple[Attempt, ...] = (),
) -> LLMResponse:
    selected = message or answer("safe fake answer")
    return LLMResponse(
        message=selected,
        finish_reason="tool_calls" if selected.tool_calls else "stop",
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        provider="deepseek",
        model="deepseek-flash",
        deployment_type=DeploymentType.CLOUD,
        latency_ms=1,
        attempts=attempts,
        cost=cost,
    )


class _SequenceGateway:
    def __init__(self, steps: Sequence[LLMResponse | Exception]) -> None:
        self.steps = list(steps)
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


@dataclass(frozen=True)
class _Observation:
    error_category: str | None = None
    task_success: bool = True
    policy_rejection_count: int = 0
    unsafe_action_attempt_count: int = 0
    unsafe_action_executed_count: int = 0
    tenant_isolation_violation_count: int = 0
    namespace_isolation_violation_count: int = 0
    unauthorized_write_execution_count: int = 0
    trusted_context_override_execution_count: int = 0


@dataclass(frozen=True)
class _Execution:
    observation: _Observation
    trace_events: tuple[AgentTraceEvent, ...] = ()


def _control() -> LiveBatchControl:
    return LiveBatchControl(currency="USD", limit=Decimal("10.00"))


@pytest.mark.parametrize("value", ["9.99", "10.00", "10.01"])
async def test_budget_gateway_accumulates_observed_cost_and_blocks_at_or_above_limit(
    value: str,
) -> None:
    delegate = _SequenceGateway([_response(_cost(value))])
    control = _control()
    gateway = ObservedCostGateway(delegate, control)

    await gateway.generate(llm_request())

    assert control.observed_known_cost == Decimal(value)
    assert control.threshold_reached is (Decimal(value) >= Decimal("10.00"))
    assert len(delegate.requests) == control.provider_generate_calls == 1
    if Decimal(value) < Decimal("10.00"):
        assert control.abort_reason is None
    else:
        assert control.abort_reason == "cost_threshold_reached"
        with pytest.raises(LiveBatchStoppedError):
            await gateway.generate(llm_request())
        assert len(delegate.requests) == 1
        assert control.provider_generate_calls == 1
        assert control.blocked_generate_calls == 1


@pytest.mark.anyio
async def test_costs_accumulate_across_responses_before_next_generate_gate() -> None:
    delegate = _SequenceGateway([_response(_cost("4.00")), _response(_cost("6.00"))])
    control = _control()
    gateway = ObservedCostGateway(delegate, control)

    await gateway.generate(llm_request())
    assert not control.threshold_reached
    await gateway.generate(llm_request())

    assert control.observed_known_cost == Decimal("10.00")
    assert control.threshold_reached
    assert control.abort_reason == "cost_threshold_reached"
    with pytest.raises(LiveBatchStoppedError):
        await gateway.generate(llm_request())
    assert len(delegate.requests) == 2
    assert control.provider_generate_calls == 2


@pytest.mark.parametrize(
    ("response_cost", "expected_reason"),
    [
        (None, "cost_observability_lost"),
        (_cost("1.00", currency="EUR"), "cost_currency_mismatch"),
    ],
)
async def test_unknown_or_mismatched_cost_fails_closed_before_next_delegate_call(
    response_cost: CostEstimate | None,
    expected_reason: str,
) -> None:
    delegate = _SequenceGateway([_response(response_cost)])
    control = _control()
    gateway = ObservedCostGateway(delegate, control)

    await gateway.generate(llm_request())

    assert control.abort_reason == expected_reason
    assert not control.cost_observable
    assert not control.cost_complete
    with pytest.raises(LiveBatchStoppedError):
        await gateway.generate(llm_request())
    assert len(delegate.requests) == 1
    assert control.provider_generate_calls == 1
    assert control.blocked_generate_calls == 1


@pytest.mark.anyio
async def test_unexpected_delegate_error_records_only_unknown_safe_category() -> None:
    delegate = _SequenceGateway([RuntimeError("SECRET_PROVIDER_BODY_MARKER")])
    control = _control()
    gateway = ObservedCostGateway(delegate, control)

    with pytest.raises(RuntimeError):
        await gateway.generate(llm_request())

    assert control.abort_reason == "provider_failure"
    assert control.provider_failure_categories == ["unknown"]
    assert not control.cost_observable
    assert not control.cost_complete
    serialized = control.to_summary().model_dump_json()
    assert "SECRET_PROVIDER_BODY_MARKER" not in serialized


@pytest.mark.anyio
async def test_successful_retry_does_not_abort_and_cost_state_is_per_run() -> None:
    retried = _response(
        _cost("0.25"),
        attempts=(
            _attempt(1, outcome="error", category=ErrorCategory.TIMEOUT, retryable=True),
            _attempt(2),
        ),
    )
    delegate = _SequenceGateway([retried, _response(_cost("0.10"), attempts=(_attempt(1),))])
    first = _control()
    first_gateway = ObservedCostGateway(delegate, first)
    second = _control()
    second_gateway = ObservedCostGateway(_SequenceGateway([_response(_cost("0.05"))]), second)

    executions = await run_controlled_cases(
        ("case-1", "case-2"),
        repetitions=1,
        control=first,
        execute_case=lambda _case, _rep: _one_generate(first_gateway),
    )
    await second_gateway.generate(llm_request())

    assert len(executions) == 2
    assert first.provider_generate_calls == 2
    assert first.provider_attempts == 3
    assert first.observed_known_cost == Decimal("0.35")
    assert first.cost_observable
    assert not first.cost_complete
    assert first.abort_reason is None
    assert first.status == "complete"
    assert second.observed_known_cost == Decimal("0.05")
    assert second.provider_generate_calls == 1


async def _one_generate(gateway: ObservedCostGateway) -> _Execution:
    await gateway.generate(llm_request())
    return _Execution(_Observation())


def _provider_error(category: ErrorCategory) -> LLMError:
    attempt = _attempt(
        1,
        outcome="error",
        category=category,
        status=401 if category is ErrorCategory.AUTHENTICATION else 400,
    )
    if category is ErrorCategory.AUTHENTICATION:
        return AuthenticationError(http_status=401, attempts=(attempt,))
    if category is ErrorCategory.BAD_REQUEST:
        return BadRequestError(http_status=400, attempts=(attempt,))
    if category is ErrorCategory.RESPONSE:
        return ProviderResponseError(attempts=(attempt,))
    if category is ErrorCategory.TIMEOUT:
        return LLMTimeoutError(http_status=408, attempts=(attempt,))
    if category is ErrorCategory.RATE_LIMIT:
        return RateLimitError(http_status=429, attempts=(attempt,))
    if category is ErrorCategory.UNAVAILABLE:
        return ProviderUnavailableError(http_status=503, attempts=(attempt,))
    if category is ErrorCategory.UNSUPPORTED:
        return UnsupportedCapabilityError(attempts=(attempt,))
    if category is ErrorCategory.CONFIGURATION:
        return ConfigurationError(attempts=(attempt,))
    return GatewayExhaustedError(attempts=(attempt,))


@pytest.mark.parametrize(
    "category",
    [
        ErrorCategory.AUTHENTICATION,
        ErrorCategory.BAD_REQUEST,
        ErrorCategory.RESPONSE,
        ErrorCategory.TIMEOUT,
        ErrorCategory.RATE_LIMIT,
        ErrorCategory.UNAVAILABLE,
        ErrorCategory.UNSUPPORTED,
        ErrorCategory.CONFIGURATION,
        ErrorCategory.EXHAUSTED,
    ],
)
async def test_terminal_provider_error_aborts_controlled_batch(category: ErrorCategory) -> None:
    control = _control()
    gateway = ObservedCostGateway(_SequenceGateway([_provider_error(category)]), control)
    started: list[str] = []

    async def execute(case: str, _repetition: int) -> _Execution:
        started.append(case)
        try:
            await gateway.generate(llm_request())
        except LLMError:
            return _Execution(_Observation(error_category="model", task_success=False))
        return _Execution(_Observation())

    results = await run_controlled_cases(
        ("first", "second", "third"), repetitions=1, control=control, execute_case=execute
    )

    assert started == ["first"]
    assert len(results) == 1
    assert control.abort_reason == "provider_failure"
    assert category.value in control.provider_failure_categories
    assert control.provider_generate_calls == 1
    assert not control.cost_observable
    assert not control.cost_complete


@pytest.mark.parametrize(
    "category", ["configuration", "model", "protocol", "runtime", "deadline", "budget"]
)
async def test_terminal_agent_error_aborts_before_next_case(category: str) -> None:
    control = _control()
    started: list[str] = []

    async def execute(case: str, _repetition: int) -> _Execution:
        started.append(case)
        return _Execution(_Observation(error_category=category, task_success=False))

    results = await run_controlled_cases(
        ("first", "second"), repetitions=1, control=control, execute_case=execute
    )

    assert started == ["first"]
    assert len(results) == 1
    assert control.abort_reason == "fatal_agent_error"
    assert control.fatal_category == category
    assert control.cases_started == control.cases_completed == 1


@pytest.mark.anyio
async def test_quality_failure_and_policy_denial_continue_to_next_case() -> None:
    control = _control()
    started: list[str] = []

    async def execute(case: str, _repetition: int) -> _Execution:
        started.append(case)
        if case == "quality-failure":
            return _Execution(_Observation(task_success=False))
        return _Execution(
            _Observation(
                policy_rejection_count=1,
                unsafe_action_attempt_count=1,
                unsafe_action_executed_count=0,
            )
        )

    results = await run_controlled_cases(
        ("quality-failure", "policy-denied"),
        repetitions=1,
        control=control,
        execute_case=execute,
    )

    assert started == ["quality-failure", "policy-denied"]
    assert len(results) == 2
    assert control.abort_reason is None
    assert control.cases_started == control.cases_completed == 2


@pytest.mark.parametrize(
    "field",
    [
        "tenant_isolation_violation_count",
        "namespace_isolation_violation_count",
        "unauthorized_write_execution_count",
        "trusted_context_override_execution_count",
    ],
)
async def test_security_invariant_aborts_before_next_case(field: str) -> None:
    control = _control()
    started: list[str] = []

    async def execute(case: str, _repetition: int) -> _Execution:
        started.append(case)
        observations = {
            "tenant_isolation_violation_count": _Observation(
                tenant_isolation_violation_count=1, task_success=False
            ),
            "namespace_isolation_violation_count": _Observation(
                namespace_isolation_violation_count=1, task_success=False
            ),
            "unauthorized_write_execution_count": _Observation(
                unauthorized_write_execution_count=1, task_success=False
            ),
            "trusted_context_override_execution_count": _Observation(
                trusted_context_override_execution_count=1, task_success=False
            ),
        }
        return _Execution(observations[field])

    results = await run_controlled_cases(
        ("unsafe", "must-not-start"), repetitions=1, control=control, execute_case=execute
    )

    assert started == ["unsafe"]
    assert len(results) == 1
    assert control.abort_reason == "security_invariant"
    assert control.fatal_category == "security"


@pytest.mark.anyio
async def test_mid_case_threshold_blocks_second_model_turn_after_tool_execution() -> None:
    costed_tool_call = response(calls(call("status-1", name="system_status"))).model_copy(
        update={"cost": _cost("10.00"), "attempts": (_attempt(1),)}
    )
    delegate = _SequenceGateway([costed_tool_call, response(answer("should not be used"))])
    control = _control()
    gateway = ObservedCostGateway(delegate, control)
    registry = ToolRegistry()

    async def ready() -> bool:
        return True

    registry.register(system_status_tool(ready))
    runtime = AgentRuntime(
        gateway,
        registry,
        ToolExecutor(registry),
        limits=AgentLimits(),
        model_policy=AgentModelPolicy(temperature=0, max_output_tokens=512),
    )

    with pytest.raises(AgentModelError):
        await runtime.run(agent_request(), context())

    assert control.threshold_reached
    assert control.abort_reason == "cost_threshold_reached"
    assert control.provider_generate_calls == 1
    assert control.blocked_generate_calls == 1
    assert len(delegate.requests) == 1


@pytest.mark.anyio
async def test_run_control_summary_is_safe_and_preserves_partial_accounting() -> None:
    control = _control()
    control.begin_case()
    control.observed_known_cost = Decimal("10.00")
    control.threshold_reached = True
    control.abort_reason = "cost_threshold_reached"
    control.fatal_category = "budget"
    control.status = "aborted"
    control.cost_complete = False
    control.provider_generate_calls = 2
    control.provider_attempts = 3
    control.blocked_generate_calls = 1
    control.cases_completed = 1
    summary = control.to_summary()
    rendered = summary.model_dump_json()

    assert summary.run_status == "aborted"
    assert summary.abort_reason == "cost_threshold_reached"
    assert summary.observed_known_cost == Decimal("10.00")
    assert not summary.cost_complete
    assert summary.provider_generate_calls == 2
    assert summary.provider_attempts == 3
    assert summary.blocked_generate_calls == 1
    assert summary.cases_started == summary.cases_completed == 1
    for marker in (
        "SECRET_QUERY_MARKER",
        "SECRET_EVIDENCE_MARKER",
        "SECRET_ASSISTANT_MARKER",
        "SECRET_ARGUMENT_MARKER",
        "SECRET_TENANT_MARKER",
        "SECRET_API_KEY_MARKER",
    ):
        assert marker not in rendered
