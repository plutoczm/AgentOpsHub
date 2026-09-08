from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.llm.config import GatewayConfig, ModelPricing, ProviderProfile, RetryPolicy
from app.llm.models import (
    DeploymentType,
    LLMRequest,
    Message,
    ModelTarget,
    Role,
    ToolCall,
    Usage,
)
from app.llm.pricing import estimate_cost
from app.llm.retry import retry_delay
from app.main import create_app

from .helpers import FAKE_KEY, profile, request


@pytest.mark.parametrize("role", list(Role))
def test_message_roles(role: Role) -> None:
    message = Message(
        role=role, content="private content", tool_call_id="call-1" if role is Role.TOOL else None
    )
    assert message.role is role
    assert "private content" not in repr(message)


@pytest.mark.parametrize(
    "data",
    [
        {"role": "unknown", "content": "x"},
        {"role": "user"},
        {"role": "tool", "content": "x"},
        {"role": "user", "content": "x", "tool_call_id": "call-1"},
        {"role": "user", "tool_calls": [{"id": "c", "name": "f", "arguments": {}}]},
    ],
)
def test_invalid_messages(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Message.model_validate(data)


@pytest.mark.parametrize(
    "change",
    [
        {"messages": []},
        {"temperature": -1},
        {"temperature": float("nan")},
        {"max_output_tokens": 0},
        {"stop": [""]},
        {"route": None},
        {"target": {"provider": "p"}},
        {"structured_schema": {"type": "array"}},
    ],
)
def test_invalid_requests(change: dict[str, object]) -> None:
    data = request().model_dump()
    data.update(change)
    with pytest.raises(ValidationError):
        LLMRequest.model_validate(data)


@pytest.mark.parametrize("kind", ["deepseek", "qwen", "generic"])
def test_vendor_profiles_are_configuration(kind: str) -> None:
    configured = ProviderProfile.model_validate({**profile().model_dump(), "kind": kind})
    assert configured.kind == kind
    assert FAKE_KEY not in repr(configured)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/v1",
        "http://private-model.internal:8000/v1",
        "http://192.168.1.30:8080/v1",
        "https://cloud.example.invalid/v1",
    ],
)
def test_configured_endpoint_classes(url: str) -> None:
    configured = profile().model_copy(update={"base_url": url})
    validated = ProviderProfile.model_validate(configured.model_dump())
    assert validated.base_url == url + "/"


@pytest.mark.parametrize(
    "url",
    [
        "file:///model",
        "https://user:password@example.invalid/v1",
        "https://example.invalid/v1?token=private",
        "https://example.invalid/#fragment",
        "https://example.invalid:bad/v1",
        "not a url",
    ],
)
def test_bad_endpoint_configuration(url: str) -> None:
    with pytest.raises(ValidationError):
        ProviderProfile.model_validate({**profile().model_dump(), "base_url": url})


def test_header_controls_and_vendor_required_key_flag() -> None:
    with pytest.raises(ValidationError) as caught:
        ProviderProfile.model_validate({**profile().model_dump(), "api_key": FAKE_KEY + "\r\n"})
    assert FAKE_KEY not in str(caught.value)
    with pytest.raises(ValidationError):
        ProviderProfile.model_validate(
            {**profile().model_dump(), "kind": "qwen", "api_key_required": False}
        )
    assert ProviderProfile.model_validate({**profile().model_dump(), "api_key": ""}).api_key is None


def test_profile_alias_and_nested_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        GatewayConfig(profiles={"wrong-alias": profile()})
    monkeypatch.setenv("AGENTOPSHUB_LLM__PROFILES__local__NAME", "local")
    monkeypatch.setenv("AGENTOPSHUB_LLM__PROFILES__local__BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("AGENTOPSHUB_LLM__PROFILES__local__DEFAULT_MODEL", "test-model")
    monkeypatch.setenv("AGENTOPSHUB_LLM__PROFILES__local__API_KEY_REQUIRED", "false")
    configured = Settings(_env_file=None).llm.profiles["local"]
    assert configured.api_key is None and not configured.api_key_required


def test_startup_with_no_providers_and_unavailable_optional_profile() -> None:
    for config in [
        GatewayConfig(),
        GatewayConfig(
            profiles={
                "local": profile("local", authenticated=False).model_copy(
                    update={
                        "base_url": "http://127.0.0.1:1/v1/",
                        "deployment_type": DeploymentType.LOCAL,
                    }
                )
            }
        ),
    ]:
        app = create_app(Settings(_env_file=None, llm=config))
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/ready").status_code == 503
            assert not any(
                path in app.openapi()["paths"] for path in ["/llm/chat", "/completion", "/proxy"]
            )


@pytest.mark.parametrize(
    "counts,expected",
    [
        ({}, (None, None, None)),
        ({"input_tokens": 10}, (10, None, None)),
        ({"input_tokens": 10, "output_tokens": 3}, (10, 3, 13)),
        ({"input_tokens": 10, "output_tokens": 3, "total_tokens": 20}, (10, 3, 20)),
    ],
)
def test_usage_semantics(counts: dict[str, int], expected: tuple[int | None, ...]) -> None:
    usage = Usage.model_validate(counts)
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == expected


@pytest.mark.parametrize("count", [-1, True, 1.5, "3"])
def test_invalid_usage(count: object) -> None:
    with pytest.raises(ValidationError):
        Usage.model_validate({"input_tokens": count})


def test_decimal_pricing_and_unknowns() -> None:
    pricing = ModelPricing(
        input_per_million=Decimal("2"), output_per_million=Decimal("6"), currency="USD"
    )
    cost = estimate_cost(Usage(input_tokens=1000, output_tokens=500), pricing)
    assert cost is not None and cost.input_cost == Decimal("0.002")
    assert cost.output_cost == Decimal("0.003") and cost.total_cost == Decimal("0.005")
    assert estimate_cost(Usage(input_tokens=1), pricing) is None
    assert estimate_cost(Usage(input_tokens=1, output_tokens=1), None) is None
    with pytest.raises(ValidationError):
        ModelPricing(
            input_per_million=Decimal("NaN"), output_per_million=Decimal("1"), currency="USD"
        )


def test_bounded_backoff_and_jitter_without_sleeping() -> None:
    policy = RetryPolicy(base_delay=1, max_delay=4, jitter=False)
    assert [retry_delay(policy, n, 0.5) for n in range(1, 6)] == [1, 2, 4, 4, 4]
    jittered = policy.model_copy(update={"jitter": True})
    assert retry_delay(jittered, 2, 0.25) == 0.5
    assert retry_delay(jittered, 1, 0.25, 3) == 3
    assert retry_delay(jittered, 1, 0.25, 1000) == 4
    assert retry_delay(jittered, 1, 0.25, float("nan")) == 0.25


def test_explicit_target_and_assistant_tool_history() -> None:
    message = Message(
        role=Role.ASSISTANT, tool_calls=(ToolCall(id="c1", name="lookup", arguments={}),)
    )
    normalized = LLMRequest(target=ModelTarget(provider="p", model="chosen"), messages=(message,))
    assert normalized.route is None and normalized.target is not None
