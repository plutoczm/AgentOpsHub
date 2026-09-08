"""Sanitized semantic errors: never retain raw provider bodies or headers."""

from app.llm.models import Attempt, ErrorCategory


class LLMError(Exception):
    """Carry a stable category and safe attempt history only."""

    category = ErrorCategory.RESPONSE
    retryable = False

    def __init__(
        self,
        *,
        http_status: int | None = None,
        retry_after: float | None = None,
        attempts: tuple[Attempt, ...] = (),
    ) -> None:
        """Use a fixed message instead of transport-provided exception text."""
        super().__init__(f"LLM operation failed: {self.category.value}")
        self.http_status = http_status
        self.retry_after = retry_after
        self.attempts = attempts


class ConfigurationError(LLMError):
    """Missing/disabled profile, missing required key, invalid route or closed gateway."""

    category = ErrorCategory.CONFIGURATION


class AuthenticationError(LLMError):
    """HTTP 401/403; never retry the same target."""

    category = ErrorCategory.AUTHENTICATION


class BadRequestError(LLMError):
    """Nonretryable request/endpoint errors, including 400, 404 and 409."""

    category = ErrorCategory.BAD_REQUEST


class RateLimitError(LLMError):
    """HTTP 429 with optional bounded Retry-After seconds."""

    category = ErrorCategory.RATE_LIMIT
    retryable = True


class ProviderUnavailableError(LLMError):
    """Connection/transport failure or provider 5xx."""

    category = ErrorCategory.UNAVAILABLE
    retryable = True


class LLMTimeoutError(LLMError):
    """HTTP timeout, HTTP 408, per-attempt deadline or overall gateway deadline."""

    category = ErrorCategory.TIMEOUT
    retryable = True


class ProviderResponseError(LLMError):
    """Malformed, oversized or semantically incompatible provider output."""

    category = ErrorCategory.RESPONSE


class UnsupportedCapabilityError(LLMError):
    """Target does not declare a feature required by the request."""

    category = ErrorCategory.UNSUPPORTED


class StructuredOutputError(LLMError):
    """Generated JSON failed local parsing/schema validation; no automatic regeneration."""

    category = ErrorCategory.STRUCTURED


class GatewayExhaustedError(LLMError):
    """All eligible candidates failed; attempts retain the individual safe categories."""

    category = ErrorCategory.EXHAUSTED
