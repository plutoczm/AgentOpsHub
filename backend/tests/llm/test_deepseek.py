import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import httpx2 as httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.agents import AgentModelPolicy, AgentRunContext, AgentRunRequest, AgentRuntime
from app.agents.models import AgentLimits
from app.core.config import Settings
from app.evaluation.live_agent import PreflightStatus, build_provider_preflight
from app.evaluation.live_agent_runner import preflight_for_settings
from app.llm.config import (
    DeepSeekOptions,
    GatewayConfig,
    ModelPricing,
    ProviderProfile,
    RetryPolicy,
    Route,
)
from app.llm.errors import UnsupportedCapabilityError
from app.llm.gateway import LLMGateway
from app.llm.models import Capabilities, DeploymentType, LLMRequest, Message, ModelTarget, Role
from app.llm.providers.openai_compatible import OpenAICompatibleProvider
from app.tools import ToolExecutor, ToolRegistry
from app.tools.builtin.system_status import system_status_tool
from tests.llm.helpers import FAKE_KEY, completion

PRICING_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"


def deepseek_profile(*, configured_mode: bool = True) -> ProviderProfile:
    return ProviderProfile(
        name="deepseek",
        kind="deepseek",
        deepseek_options=(DeepSeekOptions(thinking_mode="disabled") if configured_mode else None),
        base_url="https://api.deepseek.com",
        default_model="deepseek-flash",
        enabled=True,
        api_key=SecretStr(FAKE_KEY),
        api_key_required=True,
        deployment_type=DeploymentType.CLOUD,
        capabilities=Capabilities(tool_calling=True, temperature=True),
        pricing={
            "deepseek-flash": ModelPricing(
                input_per_million=Decimal("0.30"),
                output_per_million=Decimal("1.20"),
                currency="USD",
                effective_date=date(2026, 9, 10),
                source=PRICING_SOURCE,
            )
        },
    )


def gateway_config(profile: ProviderProfile) -> GatewayConfig:
    return GatewayConfig(
        profiles={profile.name: profile},
        routes={"agent-eval": Route(candidates=(ModelTarget(provider=profile.name),))},
        retry=RetryPolicy(max_attempts=2, jitter=False),
    )


def test_preflight_locks_single_candidate_nonthinking_and_budgets() -> None:
    profile = deepseek_profile()
    config = gateway_config(profile)
    smoke = build_provider_preflight(
        config,
        route_name="agent-eval",
        live_opt_in=False,
        limits=AgentLimits(),
        model_policy=AgentModelPolicy(temperature=0, max_output_tokens=512),
        case_count=4,
        repetitions=1,
    )
    measured = build_provider_preflight(
        config,
        route_name="agent-eval",
        live_opt_in=False,
        limits=AgentLimits(),
        model_policy=AgentModelPolicy(temperature=0, max_output_tokens=512),
        case_count=17,
        repetitions=2,
    )
    assert smoke.status is PreflightStatus.BLOCKED
    assert smoke.issues == ("live_opt_in_required",)
    assert smoke.provider_profile == "deepseek"
    assert smoke.exact_model_id == "deepseek-flash"
    assert smoke.deployment_type is DeploymentType.CLOUD
    assert smoke.tool_calling is True
    assert smoke.thinking_mode == "disabled" and smoke.non_thinking_mode is True
    assert smoke.temperature == 0 and smoke.temperature_policy == "configured"
    assert smoke.api_key_configured and smoke.pricing_configured
    assert smoke.pricing_source == PRICING_SOURCE
    assert smoke.pricing_effective_date == date(2026, 9, 10)
    assert smoke.route_candidate_count == 1
    assert smoke.gateway_attempts_per_candidate == 2
    assert smoke.max_provider_calls == 64
    assert smoke.maximum_generated_tokens == 32768
    assert smoke.output_side_cost_upper_bound == Decimal("0.0393216")
    assert smoke.projected_cost_upper_bound is None
    assert measured.max_provider_calls == 544
    assert measured.maximum_generated_tokens == 278528
    assert measured.output_side_cost_upper_bound == Decimal("0.3342336")
    assert not smoke.input_token_upper_bound_known
    assert "FAKE-UNIT-KEY-DO-NOT-USE" not in smoke.model_dump_json()


def test_preflight_rejects_multi_candidate_route_and_missing_nonthinking_config() -> None:
    profile = deepseek_profile()
    config = gateway_config(profile).model_copy(
        update={
            "routes": {
                "agent-eval": Route(
                    candidates=(
                        ModelTarget(provider="deepseek"),
                        ModelTarget(provider="deepseek", model="deepseek-flash"),
                    )
                )
            }
        }
    )
    multi = build_provider_preflight(
        config,
        route_name="agent-eval",
        live_opt_in=True,
        limits=AgentLimits(),
        model_policy=AgentModelPolicy(temperature=0),
        case_count=4,
        repetitions=1,
    )
    assert "single_candidate_route_required" in multi.issues

    missing_mode_config = gateway_config(deepseek_profile(configured_mode=False))
    missing_mode = build_provider_preflight(
        missing_mode_config,
        route_name="agent-eval",
        live_opt_in=True,
        limits=AgentLimits(),
        model_policy=AgentModelPolicy(temperature=0),
        case_count=4,
        repetitions=1,
    )
    assert missing_mode.status is PreflightStatus.BLOCKED
    assert "deepseek_non_thinking_required" in missing_mode.issues
    assert missing_mode.temperature is None
    assert missing_mode.non_thinking_mode is False


def test_enabled_thinking_mode_cannot_be_configured() -> None:
    with pytest.raises(ValidationError):
        DeepSeekOptions.model_validate({"thinking_mode": "enabled"})


def test_live_preflight_never_constructs_or_calls_gateway() -> None:
    settings = Settings(_env_file=None, llm=gateway_config(deepseek_profile()))
    with (
        patch.object(
            LLMGateway, "__init__", side_effect=AssertionError("gateway constructed")
        ) as init,
        patch.object(
            LLMGateway, "generate", side_effect=AssertionError("provider called")
        ) as generate,
    ):
        result = preflight_for_settings(
            settings,
            route_name="agent-eval",
            mode="smoke",
            live_opt_in=False,
        )
    assert result.status is PreflightStatus.BLOCKED
    assert result.issues == ("live_opt_in_required",)
    init.assert_not_called()
    generate.assert_not_called()


@pytest.mark.anyio
async def test_deepseek_serializes_nonthinking_and_completes_native_tool_loop() -> None:
    profile = deepseek_profile()
    config = gateway_config(profile)
    captured: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured.append(payload)
        assert FAKE_KEY not in request.content.decode("utf-8")
        if len(captured) == 1:
            result: Mapping[str, object] = {
                "id": "deepseek-completion-1",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "status-call",
                                    "type": "function",
                                    "function": {"name": "system_status", "arguments": "{}"},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 30,
                    "completion_tokens": 5,
                    "total_tokens": 35,
                },
            }
        else:
            result = completion(
                "The application and PostgreSQL are ok.",
                usage={"prompt_tokens": 42, "completion_tokens": 8, "total_tokens": 50},
            )
        return httpx.Response(200, json=result)

    gateway = LLMGateway(
        config,
        transport_factory=lambda _: httpx.MockTransport(handle),
    )
    registry = ToolRegistry()

    async def ready() -> bool:
        return True

    registry.register(system_status_tool(ready))
    runtime = AgentRuntime(
        gateway,
        registry,
        ToolExecutor(registry),
        model_policy=AgentModelPolicy(temperature=0, max_output_tokens=512),
    )
    try:
        result = await runtime.run(
            AgentRunRequest(user_message="Check application status.", route="agent-eval"),
            AgentRunContext(tenant_id=uuid4(), request_id=uuid4()),
        )
    finally:
        await gateway.close()

    assert result.final_message.content == "The application and PostgreSQL are ok."
    assert len(captured) == 2
    for payload in captured:
        assert payload["model"] == "deepseek-flash"
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["temperature"] == 0
        assert payload["max_tokens"] == 512
        assert payload["tools"]
        assert "reasoning_content" not in json.dumps(payload)
        assert FAKE_KEY not in json.dumps(payload)
    messages = captured[1]["messages"]
    assert isinstance(messages, list)
    assistant_turn = next(m for m in messages if isinstance(m, dict) and m.get("tool_calls"))
    tool_turn = next(m for m in messages if isinstance(m, dict) and m.get("role") == "tool")
    assert assistant_turn["tool_calls"][0]["id"] == "status-call"
    assert tool_turn["tool_call_id"] == "status-call"
    assert "reasoning_content" not in assistant_turn


@pytest.mark.anyio
async def test_deepseek_missing_nonthinking_config_fails_before_mock_transport() -> None:
    profile = deepseek_profile(configured_mode=False)
    transport_calls = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal transport_calls
        transport_calls += 1
        return httpx.Response(200, json=completion())

    provider = OpenAICompatibleProvider(
        profile,
        transport=httpx.MockTransport(handle),
    )
    try:
        with pytest.raises(UnsupportedCapabilityError):
            await provider.generate(
                LLMRequest(
                    messages=(Message(role=Role.USER, content="local test"),),
                    route="unused",
                    tools=(),
                ),
                "deepseek-flash",
                profile.capabilities,
            )
    finally:
        await provider.close()
    assert transport_calls == 0
