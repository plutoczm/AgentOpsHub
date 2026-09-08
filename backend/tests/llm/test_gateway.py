import asyncio
import json
from decimal import Decimal
from uuid import uuid4

import httpx2 as httpx
import pytest
from pydantic import BaseModel, ConfigDict

from app.llm.config import GatewayConfig, ModelPricing, RetryPolicy
from app.llm.errors import (
    AuthenticationError,
    BadRequestError,
    ConfigurationError,
    GatewayExhaustedError,
    LLMTimeoutError,
    StructuredOutputError,
    UnsupportedCapabilityError,
)
from app.llm.gateway import LLMGateway
from app.llm.models import Capabilities, DeploymentType, ErrorCategory, ModelTarget, ToolDefinition
from app.observability.logging import configure_logging, request_id_context

from .helpers import (
    FAKE_KEY,
    SleepRecorder,
    completion,
    gateway_for,
    profile,
    request,
)

pytestmark = pytest.mark.anyio


async def test_primary_success_does_not_touch_fallback() -> None:
    hosts: list[str] = []

    def handle(sent: httpx.Request) -> httpx.Response:
        hosts.append(sent.url.host)
        return httpx.Response(200, json=completion())

    async with gateway_for(handle, profiles=(profile("a"), profile("b"))) as gateway:
        result = await gateway.generate(request())
    assert hosts == ["a.example.invalid"] and result.provider == "a"
    assert len(result.attempts) == 1 and result.attempts[0].outcome == "success"
    assert result.model == "test-model"  # Do not trust arbitrary vendor-returned model labels.
    assert result.cost is None


async def test_retry_then_success_records_delays_and_attempts() -> None:
    calls = 0
    delays = SleepRecorder()

    def handle(sent: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429 if calls < 3 else 200, json=completion())

    async with gateway_for(handle, attempts=3, sleep=delays) as gateway:
        result = await gateway.generate(request())
    assert delays.delays == [1, 2]
    assert [a.sequence for a in result.attempts] == [1, 2, 3]
    assert [a.attempt_number for a in result.attempts] == [1, 2, 3]
    assert [a.error_category for a in result.attempts] == [
        ErrorCategory.RATE_LIMIT,
        ErrorCategory.RATE_LIMIT,
        None,
    ]


@pytest.mark.parametrize(
    "deployments",
    [
        (DeploymentType.CLOUD, DeploymentType.CLOUD),
        (DeploymentType.LOCAL, DeploymentType.CLOUD),
        (DeploymentType.CLOUD, DeploymentType.LOCAL),
        (DeploymentType.PRIVATE, DeploymentType.LOCAL),
    ],
)
async def test_configured_route_order_is_deployment_independent(
    deployments: tuple[DeploymentType, DeploymentType],
) -> None:
    hosts: list[str] = []

    def handle(sent: httpx.Request) -> httpx.Response:
        hosts.append(sent.url.host)
        return httpx.Response(503 if sent.url.host.startswith("a.") else 200, json=completion())

    profiles = tuple(
        profile(name, deployment=deployment, authenticated=deployment is DeploymentType.CLOUD)
        for name, deployment in zip(("a", "b"), deployments, strict=True)
    )
    async with gateway_for(handle, profiles=profiles) as gateway:
        result = await gateway.generate(request())
    assert hosts == ["a.example.invalid", "a.example.invalid", "b.example.invalid"]
    assert result.provider == "b" and result.deployment_type is deployments[1]
    assert [a.deployment_type for a in result.attempts] == [
        deployments[0],
        deployments[0],
        deployments[1],
    ]
    assert result.cost is None


async def test_multiple_fallbacks_and_exhausted_history() -> None:
    async with gateway_for(
        lambda _: httpx.Response(503, text=FAKE_KEY),
        profiles=(profile("a"), profile("b"), profile("c")),
    ) as gateway:
        with pytest.raises(GatewayExhaustedError) as caught:
            await gateway.generate(request())
    assert [a.provider for a in caught.value.attempts] == ["a", "a", "b", "b", "c", "c"]
    assert [a.sequence for a in caught.value.attempts] == list(range(1, 7))
    assert all(a.http_status == 503 and a.retryable for a in caught.value.attempts)
    assert FAKE_KEY not in repr(caught.value) + str(caught.value) + repr(caught.value.attempts)


async def test_third_candidate_success_after_distinct_failure_types() -> None:
    def handle(sent: httpx.Request) -> httpx.Response:
        if sent.url.host.startswith("a."):
            raise httpx.ReadTimeout("private transport text")
        return httpx.Response(429 if sent.url.host.startswith("b.") else 200, json=completion())

    async with gateway_for(
        handle, profiles=(profile("a"), profile("b"), profile("c")), attempts=1
    ) as gateway:
        result = await gateway.generate(request())
    assert [a.error_category for a in result.attempts] == [
        ErrorCategory.TIMEOUT,
        ErrorCategory.RATE_LIMIT,
        None,
    ]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409])
async def test_nonretryable_failure_stops_route_by_default(status: int) -> None:
    calls = 0

    def handle(sent: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    async with gateway_for(handle, profiles=(profile("a"), profile("b")), attempts=5) as gateway:
        with pytest.raises(
            AuthenticationError if status in {401, 403} else BadRequestError
        ) as caught:
            await gateway.generate(request())
    assert calls == 1 and len(caught.value.attempts) == 1


async def test_authentication_fallback_requires_explicit_opt_in() -> None:
    hosts: list[str] = []

    def handle(sent: httpx.Request) -> httpx.Response:
        hosts.append(sent.url.host)
        return httpx.Response(401 if sent.url.host.startswith("a.") else 200, json=completion())

    async with gateway_for(
        handle, profiles=(profile("a"), profile("b")), auth_fallback=True
    ) as gateway:
        result = await gateway.generate(request())
    assert hosts == ["a.example.invalid", "b.example.invalid"]
    assert result.attempts[0].error_category is ErrorCategory.AUTHENTICATION


async def test_local_only_route_and_unavailable_local() -> None:
    local = profile("local", deployment=DeploymentType.LOCAL, authenticated=False).model_copy(
        update={"base_url": "http://127.0.0.1:11434/v1/"}
    )

    def handle(sent: httpx.Request) -> httpx.Response:
        assert sent.url.host == "127.0.0.1"
        assert "authorization" not in sent.headers
        raise httpx.ConnectError("no server")

    async with gateway_for(handle, profiles=(local,), attempts=1) as gateway:
        with pytest.raises(GatewayExhaustedError) as caught:
            await gateway.generate(request())
    assert caught.value.attempts[0].error_category is ErrorCategory.UNAVAILABLE
    assert caught.value.attempts[0].deployment_type is DeploymentType.LOCAL


@pytest.mark.parametrize("feature", ["tools", "json", "structured"])
async def test_unsupported_features_fail_before_http(feature: str) -> None:
    req = request()
    if feature == "tools":
        req = req.model_copy(
            update={"tools": (ToolDefinition(name="lookup", parameters={"type": "object"}),)}
        )
    elif feature == "json":
        req = req.model_copy(update={"json_mode": True})
    else:
        req = req.model_copy(update={"structured_schema": {"type": "object"}})

    def handle(sent: httpx.Request) -> httpx.Response:
        pytest.fail("Unsupported capabilities must not contact the endpoint")

    async with gateway_for(handle, profiles=(profile(capabilities=Capabilities()),)) as gateway:
        with pytest.raises(UnsupportedCapabilityError) as caught:
            await gateway.generate(req)
    assert caught.value.attempts == ()


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str
    confidence: int


@pytest.mark.parametrize("native", [True, False])
async def test_valid_structured_output_is_locally_validated(native: bool) -> None:
    caps = Capabilities(structured_output=native, json_mode=not native)

    def handle(sent: httpx.Request) -> httpx.Response:
        body = json.loads(sent.content)
        assert body["response_format"]["type"] == ("json_schema" if native else "json_object")
        if not native:
            assert "schema" in body["messages"][0]["content"]
        return httpx.Response(200, json=completion('{"answer":"validated","confidence":3}'))

    async with gateway_for(handle, profiles=(profile(capabilities=caps),)) as gateway:
        result = await gateway.generate_structured(request=request(), response_model=Extraction)
    assert result.value.answer == "validated" and result.value.confidence == 3


@pytest.mark.parametrize(
    "text,finish",
    [
        ("not JSON", "stop"),
        ('{"answer":1,"confidence":"bad"}', "stop"),
        ('{"answer":"x","confidence":1}', "length"),
    ],
)
async def test_bad_structured_output_is_not_retried(text: str, finish: str) -> None:
    calls = 0

    def handle(sent: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=completion(text, finish=finish))

    async with gateway_for(handle) as gateway:
        with pytest.raises(StructuredOutputError) as caught:
            await gateway.generate_structured(request=request(), response_model=Extraction)
    assert calls == 1 and len(caught.value.attempts) == 1
    assert text not in str(caught.value)


async def test_usage_pricing_and_request_correlation(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    configured = profile().model_copy(
        update={
            "pricing": {
                "test-model": ModelPricing(
                    input_per_million=Decimal("2"), output_per_million=Decimal("6"), currency="USD"
                )
            }
        }
    )
    trace = uuid4()
    token = request_id_context.set(str(trace))
    try:
        async with gateway_for(
            lambda _: httpx.Response(
                200,
                json=completion(
                    "PRIVATE MODEL TEXT", usage={"prompt_tokens": 1000, "completion_tokens": 500}
                ),
            ),
            profiles=(configured,),
        ) as gateway:
            result = await gateway.generate(request())
        assert request_id_context.get() == str(trace)
    finally:
        request_id_context.reset(token)
    assert result.request_id == trace and result.cost is not None
    assert result.cost.total_cost == Decimal("0.005") and result.usage.total_tokens == 1500
    output = capsys.readouterr().out
    assert all(
        value not in output
        for value in [FAKE_KEY, "Authorization", "PRIVATE TEST PROMPT", "PRIVATE MODEL TEXT"]
    )
    events = [json.loads(line) for line in output.splitlines()]
    logged = next(event for event in events if event["event"] == "llm_attempt")
    assert logged["request_id"] == str(trace) and logged["llm_provider"] == "primary"


async def test_timeout_logs_do_not_expose_authorization(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")

    def handle(sent: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Authorization: Bearer " + FAKE_KEY)

    async with gateway_for(handle, attempts=1) as gateway:
        with pytest.raises(GatewayExhaustedError):
            await gateway.generate(request())
    output = capsys.readouterr().out
    assert (
        FAKE_KEY not in output
        and "Authorization" not in output
        and "PRIVATE TEST PROMPT" not in output
    )


async def test_global_deadline_interrupts_pending_transport() -> None:
    async def pending(sent: httpx.Request) -> httpx.Response:
        await asyncio.Future[None]()
        raise AssertionError("unreachable")

    config = GatewayConfig(profiles={"primary": profile()}, total_timeout=0.01)
    gateway = LLMGateway(config, transport_factory=lambda _: httpx.MockTransport(pending))
    try:
        with pytest.raises(LLMTimeoutError) as caught:
            await gateway.generate(
                request().model_copy(
                    update={"route": None, "target": ModelTarget(provider="primary")}
                )
            )
        assert caught.value.attempts[0].outcome == "cancelled"
    finally:
        await gateway.close()


async def test_caller_cancellation_is_not_retried() -> None:
    started = asyncio.Event()
    calls = 0

    async def pending(sent: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        started.set()
        await asyncio.Future[None]()
        raise AssertionError("unreachable")

    gateway = LLMGateway(
        GatewayConfig(profiles={"primary": profile()}),
        transport_factory=lambda _: httpx.MockTransport(pending),
    )
    try:
        task = asyncio.create_task(
            gateway.generate(
                request().model_copy(
                    update={"route": None, "target": ModelTarget(provider="primary")}
                )
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == 1
    finally:
        await gateway.close()


async def test_unknown_disabled_and_closed_gateway_fail_clearly() -> None:
    gateway = LLMGateway(
        GatewayConfig(profiles={"primary": profile().model_copy(update={"enabled": False})})
    )
    with pytest.raises(ConfigurationError):
        await gateway.generate(request())
    with pytest.raises(ConfigurationError):
        await gateway.generate(
            request().model_copy(update={"route": None, "target": ModelTarget(provider="primary")})
        )
    await gateway.close()
    await gateway.close()
    with pytest.raises(ConfigurationError):
        await gateway.generate(request())


async def test_nonstandard_json_nan_is_rejected() -> None:
    class FloatResult(BaseModel):
        number: float

    async with gateway_for(
        lambda _: httpx.Response(200, json=completion('{"number":NaN}'))
    ) as gateway:
        with pytest.raises(StructuredOutputError):
            await gateway.generate_structured(request=request(), response_model=FloatResult)


async def test_per_attempt_deadline_is_normalized() -> None:
    configured = profile()
    configured = configured.model_copy(
        update={"timeout": configured.timeout.model_copy(update={"attempt": 0.01})}
    )

    async def pending(sent: httpx.Request) -> httpx.Response:
        await asyncio.Future[None]()
        raise AssertionError("unreachable")

    gateway = LLMGateway(
        GatewayConfig(profiles={"primary": configured}, retry=RetryPolicy(max_attempts=1)),
        transport_factory=lambda _: httpx.MockTransport(pending),
    )
    try:
        with pytest.raises(GatewayExhaustedError) as caught:
            await gateway.generate(
                request().model_copy(
                    update={"route": None, "target": ModelTarget(provider="primary")}
                )
            )
        assert caught.value.attempts[0].error_category is ErrorCategory.TIMEOUT
    finally:
        await gateway.close()


async def test_explicit_target_capability_override_and_monotonic_latency() -> None:
    ticks = iter([10.0, 10.1, 10.3, 10.4])

    def handle(sent: httpx.Request) -> httpx.Response:
        assert json.loads(sent.content)["response_format"] == {"type": "json_object"}
        assert json.loads(sent.content)["model"] == "other-model"
        return httpx.Response(200, json=completion("{}"))

    gateway = LLMGateway(
        GatewayConfig(profiles={"primary": profile(capabilities=Capabilities())}),
        transport_factory=lambda _: httpx.MockTransport(handle),
        clock=lambda: next(ticks),
    )
    try:
        req = request().model_copy(
            update={
                "route": None,
                "json_mode": True,
                "target": ModelTarget(
                    provider="primary",
                    model="other-model",
                    capabilities=Capabilities(json_mode=True),
                ),
            }
        )
        result = await gateway.generate(req)
        assert result.attempts[0].latency_ms == pytest.approx(200)
        assert result.latency_ms == pytest.approx(400)
    finally:
        await gateway.close()


async def test_each_owned_transport_is_closed_even_on_failure() -> None:
    closed: list[str] = []

    class TrackedTransport(httpx.MockTransport):
        def __init__(self, name: str) -> None:
            super().__init__(lambda _: httpx.Response(200, json=completion()))
            self.name = name

        async def aclose(self) -> None:
            closed.append(self.name)
            if self.name == "a":
                raise RuntimeError(FAKE_KEY)

    gateway = LLMGateway(
        GatewayConfig(profiles={"a": profile("a"), "b": profile("b")}),
        transport_factory=lambda p: TrackedTransport(p.name),
    )
    with pytest.raises(ConfigurationError) as caught:
        await gateway.close()
    assert sorted(closed) == ["a", "b"]
    assert FAKE_KEY not in str(caught.value)


async def test_non_object_structured_schema_fails_before_transport() -> None:
    from pydantic import RootModel

    class ArrayResult(RootModel[list[str]]):
        pass

    def handle(sent: httpx.Request) -> httpx.Response:
        pytest.fail("Object schema configuration must be validated before HTTP")

    async with gateway_for(handle) as gateway:
        with pytest.raises(ConfigurationError):
            await gateway.generate_structured(request=request(), response_model=ArrayResult)
