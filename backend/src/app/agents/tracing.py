"""Bounded metadata-only traces for one AgentRuntime invocation."""

from __future__ import annotations

import re
from decimal import Decimal
from enum import StrEnum
from time import perf_counter
from uuid import UUID

from pydantic import Field

from app.llm.models import Attempt, Contract, LLMResponse
from app.tools.models import ToolEffect, ToolResult

_SAFE_LABEL = re.compile(r"^[A-Za-z0-9_./:-]{1,128}$")
_SAFE_FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")
_FINISH_REASONS = {"stop", "tool_calls", "length", "content_filter", "function_call"}
MAX_GATEWAY_ATTEMPTS_PER_TURN = 50  # Route <= 10 candidates and retries <= 5.


class AgentTraceEventType(StrEnum):
    """Small event vocabulary; events never carry model or tool payloads."""

    MODEL_TURN = "MODEL_TURN"
    GATEWAY_ATTEMPT = "GATEWAY_ATTEMPT"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    RUN_COMPLETE = "RUN_COMPLETE"
    RUN_ERROR = "RUN_ERROR"


class AgentTraceEvent(Contract):
    """Immutable, ordered, allowlisted execution metadata."""

    run_id: UUID
    request_id: UUID | None = None
    case_id: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9_-]+$")
    sequence: int = Field(ge=1)
    event_type: AgentTraceEventType
    turn_number: int | None = Field(default=None, ge=1)
    tool_calls_proposed: int | None = Field(default=None, ge=0)
    provider: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_./:-]+$")
    model: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_./:-]+$")
    deployment_type: str | None = Field(default=None, pattern=r"^(cloud|local|private)$")
    finish_reason: str | None = Field(
        default=None,
        pattern=r"^(stop|tool_calls|length|content_filter|function_call|other)$",
    )
    attempt_sequence: int | None = Field(default=None, ge=1)
    attempt_number: int | None = Field(default=None, ge=1)
    outcome: str | None = Field(default=None, pattern=r"^(success|error|cancelled)$")
    retryable: bool | None = None
    http_status: int | None = None
    tool_name: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    tool_effect: str | None = Field(default=None, pattern=r"^(read_only|write)$")
    success: bool | None = None
    error_category: str | None = Field(default=None, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    duration_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    estimated_cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    context_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    retriever_identity: str | None = Field(
        default=None, max_length=128, pattern=r"^[A-Za-z0-9_./:-]+$"
    )


class AgentTraceSummary(Contract):
    """Run totals retain unknown usage/cost as null instead of converting it to zero."""

    run_id: UUID
    request_id: UUID | None = None
    case_id: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9_-]+$")
    status: str = Field(pattern=r"^(complete|error)$")
    error_category: str | None = Field(default=None, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    model_turn_count: int = Field(ge=0)
    tool_calls_seen: int = Field(ge=0)
    tool_executions: int = Field(ge=0)
    successful_tool_count: int = Field(ge=0)
    failed_tool_count: int = Field(ge=0)
    duration_ms: float = Field(ge=0, allow_inf_nan=False)
    observed_input_tokens: int | None = Field(default=None, ge=0)
    observed_output_tokens: int | None = Field(default=None, ge=0)
    observed_total_tokens: int | None = Field(default=None, ge=0)
    known_estimated_cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    usage_complete: bool
    cost_complete: bool
    retry_count: int = Field(ge=0)


class AgentTraceRecorder:
    """Mutable collector owned by one caller/run; immutable events escape as a tuple."""

    def __init__(
        self,
        *,
        run_id: UUID,
        request_id: UUID | None,
        case_id: str | None,
        max_events: int,
        sensitive_values: tuple[str, ...] = (),
        max_tool_calls: int = 100,
    ) -> None:
        """Create a run-owned collector with a fixed event capacity."""
        if max_events < 1:
            raise ValueError("Trace event capacity must be positive")
        self.run_id = run_id
        self.request_id = request_id
        self.case_id = case_id
        self.max_events = max_events
        self._sensitive_values = tuple(value for value in sensitive_values if value)
        self._max_tool_calls = max_tool_calls
        self._events: list[AgentTraceEvent] = []
        self._started = perf_counter()
        self._summary: AgentTraceSummary | None = None
        self._tool_calls_seen = 0
        self._tool_executions = 0
        self._successful_tool_count = 0
        self._failed_tool_count = 0

    @classmethod
    def for_limits(
        cls,
        *,
        run_id: UUID,
        request_id: UUID | None,
        case_id: str | None,
        max_model_turns: int,
        max_tool_calls: int,
        sensitive_values: tuple[str, ...] = (),
    ) -> AgentTraceRecorder:
        """Allocate the proven hard bound for the runtime and gateway contracts."""
        capacity = max_model_turns * (1 + MAX_GATEWAY_ATTEMPTS_PER_TURN) + 2 * max_tool_calls + 1
        return cls(
            run_id=run_id,
            request_id=request_id,
            case_id=case_id,
            max_events=capacity,
            sensitive_values=sensitive_values,
            max_tool_calls=max_tool_calls,
        )

    @property
    def events(self) -> tuple[AgentTraceEvent, ...]:
        """Return a stable immutable snapshot of events recorded so far."""
        return tuple(self._events)

    @property
    def summary(self) -> AgentTraceSummary | None:
        """Return the finalized summary, when the run has ended."""
        return self._summary

    def record_response(
        self,
        turn_number: int,
        response: LLMResponse,
        *,
        tool_calls_proposed: int,
    ) -> None:
        """Record response metadata and its gateway attempts, never its content."""
        self._tool_calls_seen += tool_calls_proposed
        self.record_attempts(response.attempts, turn_number=turn_number)
        usage = response.usage
        cost = response.cost
        self._append(
            AgentTraceEventType.MODEL_TURN,
            turn_number=turn_number,
            tool_calls_proposed=tool_calls_proposed,
            provider=self._safe_label(response.provider),
            model=self._safe_label(response.model),
            deployment_type=response.deployment_type.value,
            finish_reason=(
                response.finish_reason if response.finish_reason in _FINISH_REASONS else "other"
            ),
            duration_ms=response.latency_ms,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            estimated_cost=cost.total_cost if cost else None,
            currency=cost.currency if cost else None,
        )

    def record_attempts(
        self,
        attempts: tuple[Attempt, ...],
        *,
        turn_number: int | None = None,
    ) -> None:
        """Record typed attempt metadata without transport bodies or exception text."""
        for attempt in attempts:
            self._append(
                AgentTraceEventType.GATEWAY_ATTEMPT,
                provider=self._safe_label(attempt.provider),
                model=self._safe_label(attempt.model),
                deployment_type=attempt.deployment_type.value,
                turn_number=turn_number,
                attempt_sequence=attempt.sequence,
                attempt_number=attempt.attempt_number,
                outcome=attempt.outcome,
                error_category=attempt.error_category.value if attempt.error_category else None,
                retryable=attempt.retryable,
                http_status=attempt.http_status,
                duration_ms=attempt.latency_ms,
            )

    def record_tool_call(self, *, name: str, effect: ToolEffect | None) -> None:
        """Record only the registered tool name and declared effect."""
        if (
            sum(event.event_type is AgentTraceEventType.TOOL_CALL for event in self._events)
            >= self._max_tool_calls
        ):
            return
        self._append(
            AgentTraceEventType.TOOL_CALL,
            tool_name=self._safe_label(name) if effect is not None else "unknown",
            tool_effect=effect.value if effect else None,
        )

    def record_tool_result(
        self,
        result: ToolResult,
        *,
        effect: ToolEffect | None,
    ) -> None:
        """Record safe result status and allowlisted knowledge fingerprints."""
        self._tool_executions += 1
        self._successful_tool_count += int(result.success)
        self._failed_tool_count += int(not result.success)
        fingerprint: str | None = None
        retriever: str | None = None
        # Only these two scalar fields may cross from knowledge_search output.
        if result.tool_name == "knowledge_search" and result.success and result.data is not None:
            raw_fingerprint = result.data.get("context_fingerprint")
            raw_retriever = result.data.get("retriever")
            if isinstance(raw_fingerprint, str) and _SAFE_FINGERPRINT.fullmatch(raw_fingerprint):
                fingerprint = raw_fingerprint
            if isinstance(raw_retriever, str):
                retriever = self._safe_label(raw_retriever)
        self._append(
            AgentTraceEventType.TOOL_RESULT,
            tool_name=self._safe_label(result.tool_name) if effect is not None else "unknown",
            tool_effect=effect.value if effect else None,
            success=result.success,
            error_category=result.error.category.value if result.error else None,
            duration_ms=result.duration_ms,
            context_fingerprint=fingerprint,
            retriever_identity=retriever,
        )

    def record_duplicate_tool_result(self, *, name: str, effect: ToolEffect | None) -> None:
        """Record a duplicate protocol result without counting an execution."""
        self._append(
            AgentTraceEventType.TOOL_RESULT,
            tool_name=self._safe_label(name) if effect is not None else "unknown",
            tool_effect=effect.value if effect else None,
            success=False,
            error_category="duplicate_tool_call",
            duration_ms=0,
        )

    def record_unexpected_tool_failure(
        self,
        *,
        name: str,
        effect: ToolEffect | None,
        duration_ms: float,
    ) -> None:
        """Record a normalized failure when an executor unexpectedly raises."""
        self._tool_executions += 1
        self._failed_tool_count += 1
        self._append(
            AgentTraceEventType.TOOL_RESULT,
            tool_name=self._safe_label(name) if effect is not None else "unknown",
            tool_effect=effect.value if effect else None,
            success=False,
            error_category="execution",
            duration_ms=duration_ms,
        )

    def finalize(
        self,
        *,
        status: str,
        error_category: str | None,
        model_turn_count: int | None = None,
    ) -> AgentTraceSummary:
        """Append one terminal event and freeze aggregates for this run."""
        if self._summary is not None:
            return self._summary
        turns = [e for e in self._events if e.event_type is AgentTraceEventType.MODEL_TURN]
        input_complete = all(e.input_tokens is not None for e in turns) and bool(turns)
        output_complete = all(e.output_tokens is not None for e in turns) and bool(turns)
        total_complete = all(e.total_tokens is not None for e in turns) and bool(turns)
        usage_complete = input_complete and output_complete and total_complete
        input_total = sum(e.input_tokens or 0 for e in turns) if input_complete else None
        output_total = sum(e.output_tokens or 0 for e in turns) if output_complete else None
        total_total = sum(e.total_tokens or 0 for e in turns) if total_complete else None
        priced = [e for e in turns if e.estimated_cost is not None and e.currency is not None]
        currencies = {e.currency for e in priced}
        has_failed_attempt = any(
            e.event_type is AgentTraceEventType.GATEWAY_ATTEMPT and e.outcome != "success"
            for e in self._events
        )
        cost_complete = (
            bool(turns)
            and len(priced) == len(turns)
            and len(currencies) == 1
            and not has_failed_attempt
        )
        known_cost = sum(
            (e.estimated_cost for e in priced if e.estimated_cost is not None), Decimal(0)
        )
        self._append(
            AgentTraceEventType.RUN_COMPLETE
            if status == "complete"
            else AgentTraceEventType.RUN_ERROR,
            error_category=error_category,
            duration_ms=max(0, (perf_counter() - self._started) * 1000),
        )
        self._summary = AgentTraceSummary(
            run_id=self.run_id,
            request_id=self.request_id,
            case_id=self.case_id,
            status=status,
            error_category=error_category,
            model_turn_count=(
                model_turn_count
                if model_turn_count is not None
                else len(
                    {
                        e.turn_number
                        for e in self._events
                        if e.event_type is AgentTraceEventType.MODEL_TURN
                        or e.event_type is AgentTraceEventType.GATEWAY_ATTEMPT
                    }
                )
            ),
            tool_calls_seen=self._tool_calls_seen,
            tool_executions=self._tool_executions,
            successful_tool_count=self._successful_tool_count,
            failed_tool_count=self._failed_tool_count,
            duration_ms=max(0, (perf_counter() - self._started) * 1000),
            observed_input_tokens=input_total,
            observed_output_tokens=output_total,
            observed_total_tokens=total_total,
            known_estimated_cost=known_cost if priced else None,
            currency=next(iter(currencies)) if len(currencies) == 1 else None,
            usage_complete=usage_complete,
            cost_complete=cost_complete,
            retry_count=sum(
                e.event_type is AgentTraceEventType.GATEWAY_ATTEMPT and (e.attempt_number or 1) > 1
                for e in self._events
            ),
        )
        return self._summary

    def _append(self, event_type: AgentTraceEventType, **fields: object) -> None:
        if len(self._events) >= self.max_events:
            raise RuntimeError("Agent trace event capacity exceeded")
        self._events.append(
            AgentTraceEvent.model_validate(
                {
                    "run_id": self.run_id,
                    "request_id": self.request_id,
                    "case_id": self.case_id,
                    "sequence": len(self._events) + 1,
                    "event_type": event_type,
                    **fields,
                }
            )
        )

    def _safe_label(self, value: str) -> str:
        """Drop any configured key substring before metadata can leave this run."""
        if any(secret in value for secret in self._sensitive_values):
            return "redacted"
        return _safe_label(value)


def _safe_label(value: str) -> str:
    """Retain configured identifiers only when they match the bounded safe alphabet."""
    return value if _SAFE_LABEL.fullmatch(value) else "other"
