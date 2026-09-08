"""Transport-independent provider boundary used by orchestration."""

from typing import Protocol

from app.llm.config import ProviderProfile
from app.llm.models import Capabilities, LLMRequest, LLMResponse, ProviderResult


class LLMProvider(Protocol):
    """One reusable async provider resource; gateway owns routing and retries."""

    @property
    def profile(self) -> ProviderProfile:
        """Return safe configuration and declared capabilities."""
        ...

    async def generate(
        self, request: LLMRequest, model: str, capabilities: Capabilities
    ) -> ProviderResult:
        """Serialize, send once and normalize; never retry internally."""
        ...

    async def close(self) -> None:
        """Release resources owned by this provider."""
        ...


class Gateway(Protocol):
    """Application-facing generation boundary, also satisfied by scripted test gateways."""

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Return normalized output; provider retries remain internal to the gateway."""
        ...
