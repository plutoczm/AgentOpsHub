"""Settings-compatible provider profiles, static routes and bounded policies."""

from datetime import date
from decimal import Decimal
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator

from app.llm.models import Capabilities, Contract, DeploymentType, Identifier, ModelTarget


class TimeoutPolicy(Contract):
    """HTTP phase timeouts plus an independent whole-attempt deadline, in seconds."""

    connect: float = Field(default=5, gt=0, le=120, allow_inf_nan=False)
    read: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    write: float = Field(default=10, gt=0, le=120, allow_inf_nan=False)
    pool: float = Field(default=5, gt=0, le=120, allow_inf_nan=False)
    attempt: float = Field(default=45, gt=0, le=600, allow_inf_nan=False)


class RetryPolicy(Contract):
    """Attempts include the initial call; jitter is bounded full jitter."""

    max_attempts: int = Field(default=2, ge=1, le=5)
    base_delay: float = Field(default=0.5, ge=0, le=60, allow_inf_nan=False)
    max_delay: float = Field(default=8, ge=0, le=120, allow_inf_nan=False)
    jitter: bool = True


class ModelPricing(Contract):
    """User-maintained rates; there is no bundled vendor price table."""

    input_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    output_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    effective_date: date | None = None
    source: str | None = Field(default=None, max_length=256, pattern=r"^[A-Za-z0-9_./:-]+$")


class ProviderProfile(Contract):
    """A cloud/local/private HTTP profile, with no eager endpoint probing."""

    name: Identifier
    kind: Literal["deepseek", "qwen", "generic"] = "generic"
    base_url: str = Field(repr=False)
    default_model: Identifier
    enabled: bool = True
    api_key: SecretStr | None = Field(default=None, repr=False)
    api_key_required: bool = True
    deployment_type: DeploymentType = DeploymentType.CLOUD
    capabilities: Capabilities = Field(default_factory=Capabilities)
    timeout: TimeoutPolicy = Field(default_factory=TimeoutPolicy)
    pricing: dict[str, ModelPricing] = Field(default_factory=dict)
    max_response_bytes: int = Field(default=2_000_000, ge=1024, le=10_000_000)

    @field_validator("base_url")
    @classmethod
    def endpoint_without_credentials(cls, value: str) -> str:
        """Allow explicitly configured HTTP(S) private endpoints, never URL credentials."""
        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or any(char.isspace() for char in value)
            ):
                raise ValueError
            _ = parsed.port
        except ValueError:
            raise ValueError(
                "Expected an HTTP(S) base URL without credentials/query/fragment"
            ) from None
        return value.rstrip("/") + "/"

    @field_validator("api_key")
    @classmethod
    def safe_header_value(cls, value: SecretStr | None) -> SecretStr | None:
        """Treat empty keys as absent and reject header control characters."""
        if value is not None:
            plain = value.get_secret_value()
            if not plain:
                return None
            if any(ord(char) < 33 or ord(char) > 126 for char in plain):
                raise ValueError("API key contains invalid header characters")
        return value

    @model_validator(mode="after")
    def cloud_profile_auth(self) -> Self:
        """Named vendor presets retain required authentication; absence fails on invocation."""
        if self.kind in {"deepseek", "qwen"} and not self.api_key_required:
            raise ValueError("Named cloud profiles require API-key authentication")
        if self.api_key is not None:
            secret = self.api_key.get_secret_value()
            if any(secret in value for value in (self.name, self.default_model, self.base_url)):
                raise ValueError("API key must not overlap provider identifiers or endpoint")
        return self


class Route(Contract):
    """Explicit candidate order is also the allowed cross-provider data path."""

    candidates: tuple[ModelTarget, ...] = Field(min_length=1, max_length=10)
    fallback_on_authentication: bool = False


class GatewayConfig(Contract):
    """Optional gateway configuration; no configured providers is a valid application state."""

    profiles: dict[str, ProviderProfile] = Field(default_factory=dict)
    routes: dict[str, Route] = Field(default_factory=dict)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    total_timeout: float = Field(default=90, gt=0, le=600, allow_inf_nan=False)

    @model_validator(mode="after")
    def matching_profile_names(self) -> Self:
        """Avoid ambiguous profile aliases without probing or requiring keys at startup."""
        if any(key != profile.name for key, profile in self.profiles.items()):
            raise ValueError("Each profile map key must equal its profile name")
        public_metadata = [
            value
            for profile in self.profiles.values()
            for value in (profile.name, profile.default_model, profile.base_url)
        ]
        public_metadata.extend(
            pricing.source
            for profile in self.profiles.values()
            for pricing in profile.pricing.values()
            if pricing.source is not None
        )
        for route_name, route in self.routes.items():
            public_metadata.append(route_name)
            for target in route.candidates:
                public_metadata.append(target.provider)
                if target.model is not None:
                    public_metadata.append(target.model)
        secrets = [
            profile.api_key.get_secret_value()
            for profile in self.profiles.values()
            if profile.api_key is not None
        ]
        if any(secret in value for secret in secrets for value in public_metadata):
            raise ValueError("API key must not overlap route or model identifiers")
        return self
