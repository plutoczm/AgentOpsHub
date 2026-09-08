from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx2 as httpx
from pydantic import SecretStr

from app.llm.config import GatewayConfig, ProviderProfile, RetryPolicy, Route
from app.llm.gateway import LLMGateway
from app.llm.models import (
    Capabilities,
    DeploymentType,
    JsonObject,
    LLMRequest,
    Message,
    ModelTarget,
    Role,
)

FAKE_KEY = "FAKE-UNIT-KEY-DO-NOT-USE"
ALL_CAPABILITIES = Capabilities(tool_calling=True, json_mode=True, structured_output=True)


def profile(
    name: str = "primary",
    *,
    deployment: DeploymentType = DeploymentType.CLOUD,
    authenticated: bool = True,
    capabilities: Capabilities = ALL_CAPABILITIES,
) -> ProviderProfile:
    return ProviderProfile(
        name=name,
        base_url=f"https://{name}.example.invalid/v1",
        default_model="test-model",
        deployment_type=deployment,
        api_key=SecretStr(FAKE_KEY) if authenticated else None,
        api_key_required=authenticated,
        capabilities=capabilities,
    )


def request(*, route: str = "test-route") -> LLMRequest:
    return LLMRequest(
        route=route, messages=(Message(role=Role.USER, content="PRIVATE TEST PROMPT"),)
    )


def completion(
    content: str = "test answer", *, usage: JsonObject | None = None, finish: str = "stop"
) -> JsonObject:
    return {
        "id": "completion-test-id",
        "model": "vendor-untrusted-model",
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish}
        ],
        "usage": usage,
    }


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


@asynccontextmanager
async def gateway_for(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    profiles: tuple[ProviderProfile, ...] = (),
    attempts: int = 2,
    auth_fallback: bool = False,
    sleep: SleepRecorder | None = None,
    total_timeout: float = 90,
) -> AsyncIterator[LLMGateway]:
    selected = profiles or (profile(),)
    config = GatewayConfig(
        profiles={item.name: item for item in selected},
        routes={
            "test-route": Route(
                candidates=tuple(ModelTarget(provider=p.name) for p in selected),
                fallback_on_authentication=auth_fallback,
            )
        },
        retry=RetryPolicy(max_attempts=attempts, base_delay=1, max_delay=4, jitter=False),
        total_timeout=total_timeout,
    )
    gateway = LLMGateway(
        config,
        transport_factory=lambda _: httpx.MockTransport(handler),
        sleep=sleep or SleepRecorder(),
    )
    try:
        yield gateway
    finally:
        await gateway.close()
