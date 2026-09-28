from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from pydantic import SecretStr

from app.agents.models import AgentLimits, AgentModelPolicy
from app.agents.tracing import AgentTraceSummary
from app.evaluation.live_agent import (
    AgentCaseObservation,
    AgentEvaluationArtifact,
    FailureCategory,
    LiveBatchControlSummary,
    LiveCaseOutcome,
    PreflightStatus,
    ProviderPreflight,
    aggregate_metrics,
    aggregate_stability,
    build_provider_preflight,
    dataset_fingerprint,
    load_live_agent_dataset,
    stable_json_hash,
)
from app.evaluation.live_agent_runner import _build_artifact
from app.llm.config import GatewayConfig, ModelPricing, ProviderProfile, RetryPolicy, Route
from app.llm.models import Capabilities, DeploymentType, ModelTarget


def _config(
    *,
    key: bool = True,
    pricing: bool = True,
    tool_calling: bool = True,
    temperature: bool = True,
) -> GatewayConfig:
    price = (
        {
            "exact-model-v2": ModelPricing(
                input_per_million=Decimal("1.00"),
                output_per_million=Decimal("2.00"),
                currency="USD",
                effective_date=date.today(),
                source="vendor-official-pricing",
            )
        }
        if pricing
        else {}
    )
    profile = ProviderProfile(
        name="eval-provider",
        kind="generic",
        base_url="https://provider.example.invalid/v1",
        default_model="exact-model-v2",
        enabled=True,
        api_key=SecretStr("SECRET_API_KEY_MARKER") if key else None,
        api_key_required=True,
        deployment_type=DeploymentType.CLOUD,
        capabilities=Capabilities(tool_calling=tool_calling, temperature=temperature),
        pricing=price,
    )
    return GatewayConfig(
        profiles={profile.name: profile},
        routes={"agent-eval": Route(candidates=(ModelTarget(provider=profile.name),))},
        retry=RetryPolicy(max_attempts=2, jitter=False),
    )


def _report(
    config: GatewayConfig,
    *,
    cases: int = 4,
    repetitions: int = 1,
) -> ProviderPreflight:
    return build_provider_preflight(
        config,
        route_name="agent-eval",
        live_opt_in=True,
        limits=AgentLimits(max_model_turns=3, max_tool_calls=2, total_timeout_seconds=45),
        model_policy=AgentModelPolicy(temperature=0, max_output_tokens=128),
        case_count=cases,
        repetitions=repetitions,
    )


def test_dataset_is_versioned_stable_and_covers_required_categories() -> None:
    dataset = load_live_agent_dataset(Path.cwd())
    assert dataset.version == "phase7b-live-agent-v1"
    assert len(dataset.cases) == 17
    categories = {case.category for case in dataset.cases}
    assert {
        "supportops_positive",
        "datacopilot_positive",
        "paraphrase",
        "ordinary_wording",
        "no_evidence",
        "unsupported_fact",
        "user_namespace_override",
        "tenant_isolation_attempt",
        "namespace_isolation_attempt",
        "malicious_retrieved_instruction",
        "knowledge_search_expected_tool",
        "system_status_expected_tool",
        "ticket_search_expected_tool",
        "denied_ticket_create",
    } <= categories
    assert dataset_fingerprint(dataset) == dataset_fingerprint(dataset)
    assert stable_json_hash({"b": 2, "a": 1}) == stable_json_hash({"a": 1, "b": 2})


def test_preflight_computes_call_and_output_token_bounds_without_key_output() -> None:
    report = _report(_config())
    assert report.status is PreflightStatus.READY
    assert report.provider_profile == "eval-provider"
    assert report.exact_model_id == "exact-model-v2"
    assert report.deployment_type is DeploymentType.CLOUD
    assert report.tool_calling is True
    assert report.api_key_configured is True
    assert report.pricing_configured is True
    assert report.pricing_effective_date == date.today()
    assert report.max_provider_calls == 4 * 1 * 3 * 1 * 2
    assert report.maximum_generated_tokens == report.max_provider_calls * 128
    assert report.maximum_batch_runtime_seconds == 4 * 45
    assert report.input_token_upper_bound_known is False
    assert report.projected_cost_upper_bound is None
    assert report.observed_cost_stop_threshold_usd == Decimal("10.00")
    assert "SECRET_API_KEY_MARKER" not in report.model_dump_json()


def test_preflight_requires_opt_in_route_profile_key_capability_and_cloud_price() -> None:
    no_route = _report(GatewayConfig(), cases=16, repetitions=2)
    assert no_route.status is PreflightStatus.REQUIRED
    assert no_route.max_provider_calls == 0
    assert no_route.maximum_generated_tokens == 0

    no_opt_in = build_provider_preflight(
        _config(),
        route_name="agent-eval",
        live_opt_in=False,
        limits=AgentLimits(),
        model_policy=AgentModelPolicy(temperature=0),
        case_count=4,
        repetitions=1,
    )
    assert no_opt_in.status is PreflightStatus.BLOCKED
    assert "live_opt_in_required" in no_opt_in.issues

    assert "api_key_required" in _report(_config(key=False)).issues
    assert "tool_calling_capability_required" in _report(_config(tool_calling=False)).issues
    assert "cloud_pricing_config_required" in _report(_config(pricing=False)).issues


def test_temperature_is_omitted_when_model_contract_does_not_support_it() -> None:
    report = _report(_config(temperature=False))
    assert report.status is PreflightStatus.READY
    assert report.temperature is None
    assert report.temperature_policy == "omitted_by_model_contract"


def test_cloud_pricing_gate_requires_a_source_and_effective_date() -> None:
    configured = _config()
    profile = configured.profiles["eval-provider"]
    unsigned = ModelPricing(
        input_per_million=Decimal("1"),
        output_per_million=Decimal("2"),
        currency="USD",
        effective_date=date.today(),
    )
    profile_without_source = profile.model_copy(update={"pricing": {"exact-model-v2": unsigned}})
    config_without_source = GatewayConfig(
        profiles={"eval-provider": profile_without_source},
        routes=configured.routes,
        retry=configured.retry,
    )
    assert "cloud_pricing_config_required" in _report(config_without_source).issues


def _observation(repetition: int, *, success: bool, tools: tuple[str, ...]) -> AgentCaseObservation:
    return AgentCaseObservation(
        case_id="stable-case",
        category="supportops_positive",
        repetition_index=repetition,
        expected_outcome=LiveCaseOutcome.ANSWER,
        actual_outcome=LiveCaseOutcome.ANSWER if success else LiveCaseOutcome.NO_EVIDENCE,
        task_success=success,
        selected_tools=tools,
        executed_tools=tools,
        tool_call_count=len(tools),
        required_tool_count=1,
        required_tool_selection_correct="knowledge_search" in tools,
        valid_tool_argument_count=len(tools),
        unnecessary_tool_call_count=0,
        unknown_tool_count=0,
        duplicate_tool_call_count=0,
        tenant_isolation_violation_count=0,
        namespace_isolation_violation_count=0,
        unauthorized_write_execution_count=0,
        trusted_context_override_execution_count=0,
        policy_rejection_count=0,
        input_validation_failure_count=0,
        expected_source_retrieved=success,
        grounded_answer=success,
        no_evidence_refusal_correct=None,
        unsupported_claim_failed=False,
        unsafe_action_attempt_count=0,
        forbidden_tool_attempt_count=0,
        unsafe_action_executed_count=0,
        failure_category=None if success else FailureCategory.WRONG_SOURCE,
        trace_summary=AgentTraceSummary(
            run_id=uuid4(),
            request_id=None,
            case_id="stable-case",
            status="complete",
            model_turn_count=2,
            tool_calls_seen=len(tools),
            tool_executions=len(tools),
            successful_tool_count=len(tools),
            failed_tool_count=0,
            duration_ms=100,
            observed_input_tokens=10,
            observed_output_tokens=5,
            observed_total_tokens=15,
            known_estimated_cost=None,
            currency=None,
            usage_complete=True,
            cost_complete=False,
            retry_count=0,
        ),
    )


def test_deterministic_metrics_and_repeat_stability_use_safe_fields_only() -> None:
    first = _observation(1, success=True, tools=("knowledge_search",))
    second = _observation(2, success=False, tools=())
    metrics = aggregate_metrics((first, second))
    stability = aggregate_stability((first, second))
    assert metrics.required_tool_selection_accuracy == 0.5
    assert metrics.task_success_rate == 0.5
    assert metrics.expected_source_retrieval_rate == 0.5
    assert stability.repetition_count == 2
    assert stability.repeated_case_count == 1
    assert stability.task_outcome_stability == 0
    assert stability.tool_choice_stability == 0
    assert stability.observed_tokens_variation == 0
    assert "final" not in repr(stability).casefold()


def test_aborted_live_artifact_preserves_only_safe_partial_control_state() -> None:
    dataset = load_live_agent_dataset(Path.cwd())
    observation = _observation(1, success=False, tools=())
    control = LiveBatchControlSummary(
        run_status="aborted",
        abort_reason="provider_failure",
        fatal_category="model",
        provider_failure_categories=("authentication",),
        currency="USD",
        cost_limit=Decimal("10.00"),
        observed_known_cost=Decimal("0"),
        cost_observable=True,
        cost_complete=False,
        threshold_reached=False,
        provider_generate_calls=1,
        provider_attempts=1,
        blocked_generate_calls=0,
        cases_started=1,
        cases_completed=1,
    )
    artifact = _build_artifact(
        mode="LIVE_BASELINE",
        dataset=dataset,
        cases=(observation,),
        traces=(),
        corpus_hash="a" * 64,
        tool_schema=(),
        limits=AgentLimits(),
        policy=AgentModelPolicy(temperature=0),
        gateway_config=GatewayConfig(),
        profile_name="deepseek",
        model="deepseek-flash",
        deployment=DeploymentType.CLOUD,
        thinking_mode="disabled",
        non_thinking_mode=True,
        pricing_source="https://api-docs.deepseek.com/quick_start/pricing",
        pricing_date=date(2026, 9, 10),
        live_batch_control=control,
    )

    assert isinstance(artifact, AgentEvaluationArtifact)
    assert artifact.schema_version == "1.1.0"
    assert artifact.run_status == "aborted"
    assert artifact.abort_reason == "provider_failure"
    assert artifact.live_batch_control == control
    serialized = artifact.model_dump_json()
    for marker in (
        "SECRET_QUERY_MARKER",
        "SECRET_EVIDENCE_MARKER",
        "SECRET_ASSISTANT_MARKER",
        "SECRET_ARGUMENT_MARKER",
        "SECRET_TENANT_MARKER",
        "SECRET_API_KEY_MARKER",
    ):
        assert marker not in serialized
