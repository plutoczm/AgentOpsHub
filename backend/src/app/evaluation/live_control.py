"""Run-local budget and fail-fast controls for live evaluation only."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Protocol

from app.agents.tracing import AgentTraceEvent, AgentTraceEventType
from app.evaluation.live_agent import LiveBatchControlSummary
from app.llm.errors import LLMError
from app.llm.models import CostEstimate, LLMRequest, LLMResponse
from app.llm.protocols import Gateway

AbortReason = Literal[
    "cost_threshold_reached",
    "cost_observability_lost",
    "cost_currency_mismatch",
    "provider_failure",
    "fatal_agent_error",
    "security_invariant",
]

_FATAL_AGENT_CATEGORIES = frozenset(
    {"configuration", "model", "protocol", "runtime", "deadline", "budget"}
)
_PROVIDER_CATEGORIES = frozenset(
    {
        "configuration",
        "authentication",
        "bad_request",
        "rate_limit",
        "unavailable",
        "timeout",
        "response",
        "unsupported_capability",
        "structured_output",
        "exhausted",
        "unknown",
    }
)


class LiveBatchStoppedError(Exception):
    """Safe internal signal that the live evaluation governor blocked a call."""

    def __init__(self) -> None:
        """Create a fixed, application-owned message without provider text."""
        super().__init__("Live evaluation safety governor stopped the batch.")


class _CaseObservation(Protocol):
    @property
    def error_category(self) -> str | None: ...

    @property
    def tenant_isolation_violation_count(self) -> int: ...

    @property
    def namespace_isolation_violation_count(self) -> int: ...

    @property
    def unauthorized_write_execution_count(self) -> int: ...

    @property
    def trusted_context_override_execution_count(self) -> int: ...


class _CaseResult(Protocol):
    @property
    def observation(self) -> _CaseObservation: ...

    @property
    def trace_events(self) -> tuple[AgentTraceEvent, ...]: ...


@dataclass(slots=True)
class LiveBatchControl:
    """Mutable accounting owned by exactly one live evaluation invocation."""

    currency: str
    limit: Decimal
    observed_known_cost: Decimal = Decimal("0")
    cost_observable: bool = True
    cost_complete: bool = True
    threshold_reached: bool = False
    provider_generate_calls: int = 0
    provider_attempts: int = 0
    blocked_generate_calls: int = 0
    cases_started: int = 0
    cases_completed: int = 0
    status: Literal["running", "complete", "aborted"] = "running"
    abort_reason: AbortReason | None = None
    fatal_category: str | None = None
    provider_failure_categories: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Reject invalid local governors before any provider call can be delegated."""
        if not self.currency.isascii() or not self.currency.isalpha() or len(self.currency) != 3:
            raise ValueError("Live evaluation budget requires an ISO-style currency code")
        self.currency = self.currency.upper()
        if not self.limit.is_finite() or self.limit <= 0:
            raise ValueError("Live evaluation cost threshold must be finite and positive")

    @property
    def should_stop(self) -> bool:
        """Whether no additional case/provider request may start."""
        return self.status != "running" or self.threshold_reached or not self.cost_observable

    def begin_case(self) -> bool:
        """Count a case only when the batch is still permitted to start it."""
        if self.should_stop:
            return False
        self.cases_started += 1
        return True

    def before_provider_generate(self) -> None:
        """Gate every model turn before the wrapped Gateway is invoked."""
        if self.should_stop:
            self.blocked_generate_calls += 1
            if self.status == "running":
                if self.threshold_reached:
                    self._abort("cost_threshold_reached", "budget")
                elif not self.cost_observable:
                    self._abort("cost_observability_lost", "budget")
            raise LiveBatchStoppedError()
        self.provider_generate_calls += 1

    def record_response_cost(self, cost: CostEstimate | None) -> None:
        """Accumulate only known configured-price estimates; unknowns stop future calls."""
        if cost is None or not cost.total_cost.is_finite() or cost.total_cost < 0:
            self.cost_observable = False
            self.cost_complete = False
            self._abort("cost_observability_lost", "budget")
            return
        if cost.currency != self.currency:
            self.cost_observable = False
            self.cost_complete = False
            self._abort("cost_currency_mismatch", "budget")
            return
        self.observed_known_cost += cost.total_cost
        if self.observed_known_cost >= self.limit:
            self.threshold_reached = True
            self._abort("cost_threshold_reached", "budget")

    def record_successful_response(self, response: LLMResponse) -> None:
        """Consume existing gateway attempt and cost metadata without retrying."""
        self.provider_attempts += len(response.attempts)
        if any(attempt.outcome != "success" for attempt in response.attempts):
            self.cost_complete = False
        self.record_response_cost(response.cost)

    def record_provider_error(self, error: LLMError) -> None:
        """Preserve safe categories from the terminal Gateway failure."""
        self.provider_attempts += len(error.attempts)
        self.cost_complete = False
        if error.attempts:
            self.cost_observable = False
        categories = [
            attempt.error_category.value
            for attempt in error.attempts
            if attempt.error_category is not None
        ]
        categories.append(error.category.value)
        self._record_provider_categories(categories)
        self._abort("provider_failure", "model")

    def record_unknown_provider_error(self) -> None:
        """Fail closed on an unexpected delegate exception without retaining its text."""
        self.cost_observable = False
        self.cost_complete = False
        self._record_provider_categories(("unknown",))
        self._abort("provider_failure", "model")

    def complete_case(
        self,
        observation: _CaseObservation,
        trace_events: tuple[AgentTraceEvent, ...],
    ) -> None:
        """Account one result and abort only for terminal errors or security invariants."""
        if self.cases_completed >= self.cases_started:
            raise RuntimeError("Live evaluation case completion was not paired with a start")
        self.cases_completed += 1
        if any(
            (
                observation.tenant_isolation_violation_count,
                observation.namespace_isolation_violation_count,
                observation.unauthorized_write_execution_count,
                observation.trusted_context_override_execution_count,
            )
        ):
            self._abort("security_invariant", "security", override=True)
            return
        if self.status != "running":
            return
        category = observation.error_category
        if category is None or category not in _FATAL_AGENT_CATEGORIES:
            return
        if category == "model":
            self._record_provider_categories(
                event.error_category
                for event in trace_events
                if event.event_type is AgentTraceEventType.GATEWAY_ATTEMPT
                and event.outcome == "error"
                and event.error_category in _PROVIDER_CATEGORIES
            )
        self._abort("fatal_agent_error", category)

    def mark_complete(self) -> None:
        """Freeze normal completion; an aborted batch remains aborted."""
        if self.status == "running":
            self.status = "complete"

    def to_summary(self) -> LiveBatchControlSummary:
        """Return a typed, payload-free snapshot for complete or partial artifacts."""
        self.mark_complete()
        return LiveBatchControlSummary(
            run_status="aborted" if self.status == "aborted" else "complete",
            abort_reason=self.abort_reason,
            fatal_category=self.fatal_category,
            provider_failure_categories=tuple(self.provider_failure_categories),
            currency=self.currency,
            cost_limit=self.limit,
            observed_known_cost=self.observed_known_cost,
            cost_observable=self.cost_observable,
            cost_complete=self.cost_complete,
            threshold_reached=self.threshold_reached,
            provider_generate_calls=self.provider_generate_calls,
            provider_attempts=self.provider_attempts,
            blocked_generate_calls=self.blocked_generate_calls,
            cases_started=self.cases_started,
            cases_completed=self.cases_completed,
        )

    def _record_provider_categories(self, categories: Iterable[str]) -> None:
        for category in categories:
            if (
                category in _PROVIDER_CATEGORIES
                and category not in self.provider_failure_categories
            ):
                self.provider_failure_categories.append(category)

    def _abort(self, reason: AbortReason, fatal_category: str, *, override: bool = False) -> None:
        if self.abort_reason is None or override:
            self.abort_reason = reason
            self.fatal_category = fatal_category
        self.status = "aborted"


class ObservedCostGateway:
    """Evaluation-only Gateway decorator that enforces observed-cost stops per turn."""

    def __init__(self, delegate: Gateway, control: LiveBatchControl) -> None:
        """Wrap one run-owned Gateway; the delegate retains HTTP, routing and retries."""
        self._delegate = delegate
        self._control = control

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Block after observed stop conditions, otherwise delegate once without retrying."""
        self._control.before_provider_generate()
        try:
            response = await self._delegate.generate(request)
        except asyncio.CancelledError:
            raise
        except LLMError as exc:
            self._control.record_provider_error(exc)
            raise
        except Exception:
            self._control.record_unknown_provider_error()
            raise
        self._control.record_successful_response(response)
        return response


async def run_controlled_cases[CaseT, ResultT: _CaseResult](
    cases: Sequence[CaseT],
    *,
    repetitions: int,
    control: LiveBatchControl,
    execute_case: Callable[[CaseT, int], Awaitable[ResultT]],
) -> tuple[ResultT, ...]:
    """Run cases in order and never start a new case after a run-local abort."""
    if repetitions < 1:
        raise ValueError("Live batch repetitions must be positive")
    executions: list[ResultT] = []
    for repetition in range(1, repetitions + 1):
        for case in cases:
            if not control.begin_case():
                break
            execution = await execute_case(case, repetition)
            executions.append(execution)
            control.complete_case(execution.observation, execution.trace_events)
            if control.should_stop:
                break
        if control.should_stop:
            break
    control.mark_complete()
    return tuple(executions)
