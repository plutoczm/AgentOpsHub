"""Versioned live-agent evaluation contracts, deterministic graders and preflight."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from app.agents.models import AgentLimits, AgentModelPolicy
from app.agents.tracing import AgentTraceEvent, AgentTraceSummary
from app.llm.config import GatewayConfig, ModelPricing, ProviderProfile, Route
from app.llm.models import Contract, DeploymentType


class LiveCaseOutcome(StrEnum):
    """Deterministic behavior class expected from one case."""

    ANSWER = "ANSWER"
    NO_EVIDENCE = "NO_EVIDENCE"
    POLICY_DENIED = "POLICY_DENIED"
    TOOL_RESULT = "TOOL_RESULT"
    SAFE_FAILURE = "SAFE_FAILURE"


class FailureCategory(StrEnum):
    """Closed failure taxonomy used in case-level artifacts."""

    WRONG_TOOL = "wrong_tool"
    MISSING_TOOL = "missing_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    POLICY_DENIED = "policy_denied"
    RETRIEVAL_NO_EVIDENCE = "retrieval_no_evidence"
    WRONG_SOURCE = "wrong_source"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    UNSAFE_ACTION_ATTEMPT = "unsafe_action_attempt"
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"
    BUDGET_EXCEEDED = "budget_exceeded"
    PROTOCOL_ERROR = "protocol_error"
    UNKNOWN = "unknown"


class AgentCorpusDocument(Contract):
    """One versioned fixture path with synthetic trusted scope labels."""

    source_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_-]+$")
    tenant_ref: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    namespace: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=200, repr=False)
    path: str = Field(pattern=r"^[a-zA-Z0-9_./-]+$")

    @model_validator(mode="after")
    def safe_path(self) -> Self:
        """Reject absolute, traversing, or non-Markdown fixture paths."""
        if (
            self.path.startswith("/")
            or "\\" in self.path
            or any(part in {"", ".", ".."} for part in self.path.split("/"))
        ):
            raise ValueError("Invalid evaluation fixture path")
        if not self.path.endswith(".md"):
            raise ValueError("Evaluation fixtures must be Markdown")
        return self


class LiveAgentCase(Contract):
    """Case contract keeps expected outcomes and tool policy separate from answer text."""

    case_id: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    category: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    tenant_ref: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    namespace: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    user_task: str = Field(min_length=1, max_length=4000, repr=False)
    search_query: str | None = Field(default=None, max_length=512, repr=False)
    expected_outcome: LiveCaseOutcome
    required_tools: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    expected_sources: tuple[str, ...] = ()
    required_answer_terms: tuple[str, ...] = Field(default=(), repr=False)
    refusal_terms: tuple[str, ...] = Field(default=(), repr=False)
    forbidden_answer_terms: tuple[str, ...] = Field(default=(), repr=False)
    expected_tool_error: str | None = None
    expected_evidence: bool | None = None

    @model_validator(mode="after")
    def identity_and_scope(self) -> Self:
        """Keep tool/source labels unique and evidence outcomes internally consistent."""
        for group in (self.required_tools, self.allowed_tools, self.forbidden_tools):
            if len(set(group)) != len(group):
                raise ValueError("Tool labels must be unique")
            if any(not item or len(item) > 64 for item in group):
                raise ValueError("Invalid tool label")
        if len(set(self.expected_sources)) != len(self.expected_sources):
            raise ValueError("Expected sources must be unique")
        if self.expected_evidence and not self.expected_sources:
            raise ValueError("Evidence cases need expected source labels")
        if self.expected_outcome is LiveCaseOutcome.NO_EVIDENCE and self.expected_evidence:
            raise ValueError("No-evidence cases cannot expect evidence")
        return self


class LiveAgentDataset(Contract):
    """Stable case and corpus manifest, isolated from Phase 6 and Phase 7A data."""

    name: str
    version: str
    description: str = Field(repr=False)
    corpus: tuple[AgentCorpusDocument, ...] = Field(min_length=1, max_length=100)
    cases: tuple[LiveAgentCase, ...] = Field(min_length=12, max_length=24)

    @model_validator(mode="after")
    def references(self) -> Self:
        """Reject duplicate identities and references outside the declared fixture corpus."""
        source_keys = {doc.source_key for doc in self.corpus}
        case_ids = [case.case_id for case in self.cases]
        if len(source_keys) != len(self.corpus) or len(set(case_ids)) != len(case_ids):
            raise ValueError("Dataset identities must be unique")
        if any(not set(case.expected_sources) <= source_keys for case in self.cases):
            raise ValueError("Case references an unknown source")
        if any(
            case.tenant_ref not in {doc.tenant_ref for doc in self.corpus} for case in self.cases
        ):
            raise ValueError("Case references an unknown tenant label")
        return self


def load_live_agent_dataset(root: Path) -> LiveAgentDataset:
    """Load the explicitly versioned agent dataset and confine fixtures below root."""
    manifest = root / "evaluation/agent/phase7b-live-agent-v1.json"
    dataset = LiveAgentDataset.model_validate_json(manifest.read_bytes())
    resolved_root = root.resolve()
    for doc in dataset.corpus:
        fixture = (root / doc.path).resolve()
        if not fixture.is_relative_to(resolved_root) or not fixture.is_file():
            raise ValueError("Missing or out-of-root evaluation fixture")
    return dataset


class PreflightStatus(StrEnum):
    """Local provider configuration and opt-in gate result."""

    READY = "ready_for_approval"
    REQUIRED = "configuration_required"
    BLOCKED = "blocked"


class ProviderPreflight(Contract):
    """Safe local-only provider and budget inspection; never probes a provider."""

    status: PreflightStatus
    live_opt_in: bool
    route: str = Field(pattern=r"^[A-Za-z0-9_./:-]{1,128}$")
    provider_profile: str | None = None
    exact_model_id: str | None = None
    deployment_type: DeploymentType | None = None
    tool_calling: bool | None = None
    api_key_configured: bool
    pricing_configured: bool
    pricing_source: str | None = None
    pricing_effective_date: date | None = None
    temperature: float | None = None
    temperature_policy: Literal["configured", "omitted_by_model_contract"]
    max_output_tokens: int
    max_model_turns: int
    max_tool_calls: int
    deadline_seconds: float
    maximum_batch_runtime_seconds: float
    case_count: int
    repetitions: int
    route_candidate_count: int
    gateway_attempts_per_candidate: int
    max_provider_calls: int
    maximum_generated_tokens: int
    input_token_upper_bound_known: bool = False
    projected_cost_upper_bound: Decimal | None = None
    suggested_observed_cost_stop_usd: Decimal = Decimal("10.00")
    currency: str | None = None
    issues: tuple[str, ...] = ()


def build_provider_preflight(
    config: GatewayConfig,
    *,
    route_name: str,
    live_opt_in: bool,
    limits: AgentLimits,
    model_policy: AgentModelPolicy,
    case_count: int,
    repetitions: int,
) -> ProviderPreflight:
    """Resolve local config and worst-case calls/tokens without network access."""
    if case_count < 1 or repetitions < 1:
        raise ValueError("Case count and repetitions must be positive")
    route: Route | None = config.routes.get(route_name)
    issues: list[str] = []
    candidates: list[tuple[ProviderProfile, str, bool, ModelPricing | None]] = []
    temperature_capabilities: list[bool] = []
    if route is None:
        issues.append("route_missing")
    else:
        for target in route.candidates:
            profile = config.profiles.get(target.provider)
            if profile is None:
                issues.append("provider_profile_missing")
                continue
            model = target.model or profile.default_model
            capabilities = target.capabilities or profile.capabilities
            temperature_capabilities.append(capabilities.temperature)
            candidates.append(
                (profile, model, capabilities.tool_calling, profile.pricing.get(model))
            )
            if not profile.enabled:
                issues.append("provider_disabled")
            if not capabilities.tool_calling:
                issues.append("tool_calling_capability_required")
            if profile.api_key_required and profile.api_key is None:
                issues.append("api_key_required")
            if profile.deployment_type is DeploymentType.CLOUD:
                pricing = profile.pricing.get(model)
                if pricing is None or pricing.effective_date is None or pricing.source is None:
                    issues.append("cloud_pricing_config_required")
                elif pricing.effective_date > date.today():
                    issues.append("pricing_not_yet_effective")
    if not live_opt_in:
        issues.append("live_opt_in_required")
    candidate_count = len(route.candidates) if route is not None else 0
    attempts = config.retry.max_attempts
    max_calls = case_count * repetitions * limits.max_model_turns * candidate_count * attempts
    max_tokens = max_calls * model_policy.max_output_tokens
    primary = candidates[0] if candidates else None
    key_configured = bool(
        primary and primary[0].api_key is not None and primary[0].api_key.get_secret_value()
    )
    cloud_candidates = [
        (profile, pricing)
        for profile, _, _, pricing in candidates
        if profile.deployment_type is DeploymentType.CLOUD
    ]
    pricing_configured = bool(cloud_candidates) and all(
        pricing is not None and pricing.effective_date is not None and pricing.source is not None
        for _, pricing in cloud_candidates
    )
    pricing_dates = {
        pricing.effective_date
        for _, _, _, pricing in candidates
        if pricing is not None and pricing.effective_date is not None
    }
    currencies = {pricing.currency for _, _, _, pricing in candidates if pricing is not None}
    resolved_temperature = (
        model_policy.temperature
        if not temperature_capabilities or all(temperature_capabilities)
        else None
    )
    projected: Decimal | None = None
    if (
        candidates
        and len(currencies) == 1
        and all(
            profile.deployment_type is not DeploymentType.CLOUD
            or (pricing is not None and pricing.input_per_million == 0)
            for profile, _, _, pricing in candidates
        )
    ):
        output_costs = [
            pricing.output_per_million for _, _, _, pricing in candidates if pricing is not None
        ]
        if output_costs:
            projected = (
                max(output_costs)
                * Decimal(model_policy.max_output_tokens)
                * Decimal(max_calls)
                / Decimal(1_000_000)
            )
    status = (
        PreflightStatus.REQUIRED
        if "route_missing" in issues or "provider_profile_missing" in issues or not candidates
        else PreflightStatus.BLOCKED
        if issues
        else PreflightStatus.READY
    )
    return ProviderPreflight(
        status=status,
        live_opt_in=live_opt_in,
        route=route_name,
        provider_profile=primary[0].name if primary else None,
        exact_model_id=primary[1] if primary else None,
        deployment_type=primary[0].deployment_type if primary else None,
        tool_calling=primary[2] if primary else None,
        api_key_configured=key_configured,
        pricing_configured=pricing_configured,
        pricing_source=primary[3].source if primary and primary[3] else None,
        pricing_effective_date=(next(iter(pricing_dates)) if len(pricing_dates) == 1 else None),
        temperature=resolved_temperature,
        temperature_policy=(
            "configured" if resolved_temperature is not None else "omitted_by_model_contract"
        ),
        max_output_tokens=model_policy.max_output_tokens,
        max_model_turns=limits.max_model_turns,
        max_tool_calls=limits.max_tool_calls,
        deadline_seconds=limits.total_timeout_seconds,
        maximum_batch_runtime_seconds=case_count * repetitions * limits.total_timeout_seconds,
        case_count=case_count,
        repetitions=repetitions,
        route_candidate_count=candidate_count,
        gateway_attempts_per_candidate=attempts,
        max_provider_calls=max_calls,
        maximum_generated_tokens=max_tokens,
        projected_cost_upper_bound=projected,
        currency=next(iter(currencies)) if len(currencies) == 1 else None,
        issues=tuple(dict.fromkeys(issues)),
    )


class AgentCaseObservation(Contract):
    """Payload-free case result for review and aggregation."""

    case_id: str
    category: str
    repetition_index: int = Field(ge=1)
    expected_outcome: LiveCaseOutcome
    actual_outcome: LiveCaseOutcome
    task_success: bool
    selected_tools: tuple[str, ...] = ()
    executed_tools: tuple[str, ...] = ()
    tool_call_count: int = Field(ge=0)
    required_tool_count: int = Field(ge=0)
    required_tool_selection_correct: bool
    valid_tool_argument_count: int = Field(ge=0)
    unnecessary_tool_call_count: int = Field(ge=0)
    unknown_tool_count: int = Field(ge=0)
    duplicate_tool_call_count: int = Field(ge=0)
    tenant_isolation_violation_count: int = Field(ge=0)
    namespace_isolation_violation_count: int = Field(ge=0)
    unauthorized_write_execution_count: int = Field(ge=0)
    trusted_context_override_execution_count: int = Field(ge=0)
    policy_rejection_count: int = Field(ge=0)
    input_validation_failure_count: int = Field(ge=0)
    expected_source_retrieved: bool | None = None
    grounded_answer: bool | None = None
    no_evidence_refusal_correct: bool | None = None
    unsupported_claim_failed: bool
    unsafe_action_attempt_count: int = Field(ge=0)
    forbidden_tool_attempt_count: int = Field(ge=0)
    unsafe_action_executed_count: int = Field(ge=0)
    failure_category: FailureCategory | None = None
    error_category: str | None = None
    trace_summary: AgentTraceSummary


class AgentEvaluationMetrics(Contract):
    """Deterministic counts and rates; no model-as-judge scores."""

    case_count: int
    task_success_count: int
    task_success_rate: float
    required_tool_selection_accuracy: float
    forbidden_tool_call_rate: float
    unnecessary_tool_call_rate: float
    valid_tool_argument_rate: float
    tool_input_validation_failure_count: int
    unknown_tool_count: int
    duplicate_tool_call_count: int
    policy_rejection_count: int
    expected_source_retrieval_rate: float | None
    answerable_task_success_rate: float | None
    no_evidence_refusal_accuracy: float | None
    unsupported_claim_failure_count: int
    unsafe_action_attempt_count: int
    unsafe_action_executed_count: int
    tenant_isolation_violations: int
    namespace_isolation_violations: int
    unauthorized_write_executions: int
    trusted_context_override_executions: int
    average_model_turns: float | None
    average_latency_ms: float | None


class AgentStabilityMetrics(Contract):
    """Repeat-to-repeat agreement without comparing or storing final answer text."""

    repetition_count: int = Field(ge=1)
    repeated_case_count: int = Field(ge=0)
    task_outcome_stability: float | None
    tool_choice_stability: float | None
    argument_validation_stability: float | None
    source_selection_stability: float | None
    model_turns_variation: int | None
    tool_executions_variation: int | None
    observed_tokens_variation: int | None
    latency_variation_ms: float | None


def aggregate_metrics(observations: tuple[AgentCaseObservation, ...]) -> AgentEvaluationMetrics:
    """Aggregate exact event/grader evidence with explicit denominators."""
    total = len(observations)
    if total == 0:
        raise ValueError("Cannot aggregate an empty evaluation")
    required_tool_cases = [item for item in observations if item.required_tool_count > 0]
    source_items = [item for item in observations if item.expected_source_retrieved is not None]
    refusal_items = [item for item in observations if item.no_evidence_refusal_correct is not None]
    answerable = [item for item in observations if item.expected_outcome is LiveCaseOutcome.ANSWER]
    tool_error_count = sum(item.input_validation_failure_count for item in observations)
    policy_count = sum(item.policy_rejection_count for item in observations)
    unsafe_attempts = sum(item.unsafe_action_attempt_count for item in observations)
    unsafe_executed = sum(item.unsafe_action_executed_count for item in observations)
    selected_count = sum(item.tool_call_count for item in observations)
    forbidden_attempts = sum(item.forbidden_tool_attempt_count for item in observations)
    required_hit_rate = (
        sum(item.required_tool_selection_correct for item in required_tool_cases)
        / len(required_tool_cases)
        if required_tool_cases
        else 1.0
    )
    total_turns = sum(item.trace_summary.model_turn_count for item in observations)
    total_latency = sum(item.trace_summary.duration_ms for item in observations)
    return AgentEvaluationMetrics(
        case_count=total,
        task_success_count=sum(item.task_success for item in observations),
        task_success_rate=sum(item.task_success for item in observations) / total,
        required_tool_selection_accuracy=required_hit_rate,
        forbidden_tool_call_rate=forbidden_attempts / selected_count if selected_count else 0.0,
        unnecessary_tool_call_rate=(
            sum(item.unnecessary_tool_call_count for item in observations) / selected_count
            if selected_count
            else 0.0
        ),
        valid_tool_argument_rate=(
            sum(item.valid_tool_argument_count for item in observations) / selected_count
            if selected_count
            else 1.0
        ),
        tool_input_validation_failure_count=tool_error_count,
        unknown_tool_count=sum(item.unknown_tool_count for item in observations),
        duplicate_tool_call_count=sum(item.duplicate_tool_call_count for item in observations),
        policy_rejection_count=policy_count,
        expected_source_retrieval_rate=(
            sum(item.expected_source_retrieved is True for item in source_items) / len(source_items)
            if source_items
            else None
        ),
        answerable_task_success_rate=(
            sum(item.task_success for item in answerable) / len(answerable) if answerable else None
        ),
        no_evidence_refusal_accuracy=(
            sum(item.no_evidence_refusal_correct is True for item in refusal_items)
            / len(refusal_items)
            if refusal_items
            else None
        ),
        unsupported_claim_failure_count=sum(item.unsupported_claim_failed for item in observations),
        unsafe_action_attempt_count=unsafe_attempts,
        unsafe_action_executed_count=unsafe_executed,
        tenant_isolation_violations=sum(
            item.tenant_isolation_violation_count for item in observations
        ),
        namespace_isolation_violations=sum(
            item.namespace_isolation_violation_count for item in observations
        ),
        unauthorized_write_executions=sum(
            item.unauthorized_write_execution_count for item in observations
        ),
        trusted_context_override_executions=sum(
            item.trusted_context_override_execution_count for item in observations
        ),
        average_model_turns=total_turns / total,
        average_latency_ms=total_latency / total,
    )


def aggregate_stability(
    observations: tuple[AgentCaseObservation, ...],
) -> AgentStabilityMetrics:
    """Compare repeat outputs through typed outcomes and counts only."""
    groups: dict[str, list[AgentCaseObservation]] = {}
    for item in observations:
        groups.setdefault(item.case_id, []).append(item)
    repeated = [
        sorted(values, key=lambda item: item.repetition_index) for values in groups.values()
    ]
    repeated = [values for values in repeated if len(values) > 1]
    repetition_count = max((len(values) for values in groups.values()), default=1)
    if not repeated:
        return AgentStabilityMetrics(
            repetition_count=repetition_count,
            repeated_case_count=0,
            task_outcome_stability=None,
            tool_choice_stability=None,
            argument_validation_stability=None,
            source_selection_stability=None,
            model_turns_variation=None,
            tool_executions_variation=None,
            observed_tokens_variation=None,
            latency_variation_ms=None,
        )
    pairs = [(values[0], values[-1]) for values in repeated]
    task_stable = [left.task_success == right.task_success for left, right in pairs]
    tools_stable = [left.selected_tools == right.selected_tools for left, right in pairs]
    args_stable = [
        left.input_validation_failure_count == right.input_validation_failure_count
        and left.valid_tool_argument_count == right.valid_tool_argument_count
        for left, right in pairs
    ]
    source_stable = [
        left.expected_source_retrieved == right.expected_source_retrieved
        and left.grounded_answer == right.grounded_answer
        for left, right in pairs
    ]
    turn_counts = [item.trace_summary.model_turn_count for values in repeated for item in values]
    executions = [item.trace_summary.tool_executions for values in repeated for item in values]
    token_counts = [
        item.trace_summary.observed_total_tokens
        for values in repeated
        for item in values
        if item.trace_summary.observed_total_tokens is not None
    ]
    latencies = [item.trace_summary.duration_ms for values in repeated for item in values]
    return AgentStabilityMetrics(
        repetition_count=repetition_count,
        repeated_case_count=len(repeated),
        task_outcome_stability=sum(task_stable) / len(task_stable),
        tool_choice_stability=sum(tools_stable) / len(tools_stable),
        argument_validation_stability=sum(args_stable) / len(args_stable),
        source_selection_stability=sum(source_stable) / len(source_stable),
        model_turns_variation=max(turn_counts) - min(turn_counts),
        tool_executions_variation=max(executions) - min(executions),
        observed_tokens_variation=max(token_counts) - min(token_counts) if token_counts else None,
        latency_variation_ms=max(latencies) - min(latencies) if latencies else None,
    )


class AgentEvaluationManifest(Contract):
    """Reproducibility metadata without raw prompts, URLs, credentials, or tenant UUIDs."""

    evaluation_version: str
    dataset_name: str
    dataset_version: str
    dataset_sha256: str
    code_commit_sha: str
    dirty_state: bool
    corpus_sha256: str
    retriever_identity: str
    context_policy: dict[str, object]
    system_instruction_sha256: str
    tool_schema_sha256: str
    provider_profile: str | None = None
    exact_model_id: str | None = None
    deployment_type: DeploymentType | None = None
    temperature: float | None = None
    max_output_tokens: int
    agent_limits: AgentLimits
    gateway_retry_attempts: int
    gateway_timeout_seconds: float
    pricing_source: str | None = None
    pricing_effective_date: date | None = None
    run_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentEvaluationArtifact(Contract):
    """Ignored local JSON report containing only manifest, metrics and safe traces."""

    schema_version: str = "1.0.0"
    mode: Literal["HARNESS_VALIDATION", "LIVE_BASELINE"]
    manifest: AgentEvaluationManifest
    preflight: ProviderPreflight | None = None
    metrics: AgentEvaluationMetrics
    stability: AgentStabilityMetrics | None = None
    cases: tuple[AgentCaseObservation, ...]
    traces: tuple[AgentTraceEvent, ...]


def stable_json_hash(value: object) -> str:
    """Hash canonical UTF-8 JSON, independent of key insertion order or repr()."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def dataset_fingerprint(dataset: LiveAgentDataset) -> str:
    """Return the canonical SHA-256 identity of the typed dataset."""
    return stable_json_hash(dataset.model_dump(mode="json"))


def utc_now() -> datetime:
    """Return an aware UTC timestamp for evaluation metadata."""
    return datetime.now(UTC)
