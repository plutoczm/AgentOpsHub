"""Preflight checks requiring no network access."""

from app.llm.config import ProviderProfile
from app.llm.errors import ConfigurationError, UnsupportedCapabilityError
from app.llm.models import Capabilities, LLMRequest


def validate_target(
    profile: ProviderProfile, request: LLMRequest, capabilities: Capabilities
) -> None:
    """Reject absent credentials and unsupported features before a transport attempt."""
    if not profile.enabled or (profile.api_key_required and profile.api_key is None):
        raise ConfigurationError()
    if request.tools and not capabilities.tool_calling:
        raise UnsupportedCapabilityError()
    if request.json_mode and request.structured_schema is None and not capabilities.json_mode:
        raise UnsupportedCapabilityError()
    if request.structured_schema is not None and not (
        capabilities.structured_output or capabilities.json_mode
    ):
        raise UnsupportedCapabilityError()
