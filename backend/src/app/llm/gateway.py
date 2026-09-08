"""Static routing, bounded retry/fallback and privacy-safe in-memory accounting."""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from contextlib import suppress
from time import perf_counter
from typing import Literal
from uuid import UUID

import httpx2 as httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.llm.capabilities import validate_target
from app.llm.config import GatewayConfig, ProviderProfile, Route
from app.llm.errors import (
    AuthenticationError,
    ConfigurationError,
    GatewayExhaustedError,
    LLMError,
    LLMTimeoutError,
    StructuredOutputError,
)
from app.llm.models import (
    Attempt,
    Capabilities,
    JsonObject,
    LLMRequest,
    LLMResponse,
    ModelTarget,
    StructuredResult,
)
from app.llm.pricing import estimate_cost
from app.llm.protocols import LLMProvider
from app.llm.providers.openai_compatible import OpenAICompatibleProvider
from app.llm.retry import retry_delay
from app.llm.validation import validate_json_syntax
from app.observability.logging import request_id_context

logger = logging.getLogger(__name__)
TransportFactory = Callable[[ProviderProfile], httpx.AsyncBaseTransport]


class LLMGateway:
    """Optional internal service; configured routes alone decide cloud/local ordering."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        transport_factory: TransportFactory | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = perf_counter,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        """Allocate reusable clients and deterministic test seams without contacting endpoints."""
        self.config = config
        self._sleep = sleep
        self._clock = clock
        self._random = random_value
        self._closed = False
        self._providers: dict[str, LLMProvider] = {
            name: OpenAICompatibleProvider(
                profile,
                transport=transport_factory(profile) if transport_factory is not None else None,
            )
            for name, profile in config.profiles.items()
            if profile.enabled
        }

    def _route(self, request: LLMRequest) -> Route:
        if self._closed:
            raise ConfigurationError()
        if request.target is not None:
            return Route(candidates=(request.target,))
        route = self.config.routes.get(request.route or "")
        if route is None:
            raise ConfigurationError()
        return route

    def _target(self, target: ModelTarget) -> tuple[LLMProvider, str, Capabilities]:
        provider = self._providers.get(target.provider)
        if provider is None:
            raise ConfigurationError()
        return (
            provider,
            target.model or provider.profile.default_model,
            target.capabilities or provider.profile.capabilities,
        )

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Run ordered candidates within one deadline, inheriting optional HTTP correlation."""
        route = self._route(request)
        request_id = request.request_id
        if request_id is None and request_id_context.get() is not None:
            with suppress(ValueError):
                request_id = UUID(request_id_context.get() or "")
        token = request_id_context.set(str(request_id) if request_id else None)
        attempts: list[Attempt] = []
        started = self._clock()
        try:
            async with asyncio.timeout(self.config.total_timeout):
                return await self._run(request, route, attempts, started, request_id)
        except TimeoutError:
            raise LLMTimeoutError(attempts=tuple(attempts)) from None
        except LLMError as exc:
            exc.attempts = tuple(attempts)
            raise
        finally:
            request_id_context.reset(token)

    async def _run(
        self,
        request: LLMRequest,
        route: Route,
        attempts: list[Attempt],
        started: float,
        request_id: UUID | None,
    ) -> LLMResponse:
        for target in route.candidates:
            provider, model, capabilities = self._target(target)
            validate_target(provider.profile, request, capabilities)
            for number in range(1, self.config.retry.max_attempts + 1):
                attempt_start = self._clock()
                failure: LLMError | None = None
                try:
                    result = await provider.generate(request, model, capabilities)
                except asyncio.CancelledError:
                    self._record(
                        attempts, provider.profile, model, number, attempt_start, "cancelled"
                    )
                    raise
                except LLMError as exc:
                    failure = exc
                    self._record(
                        attempts, provider.profile, model, number, attempt_start, "error", exc
                    )
                else:
                    self._record(
                        attempts, provider.profile, model, number, attempt_start, "success"
                    )
                    cost = estimate_cost(result.usage, provider.profile.pricing.get(model))
                    logger.info(
                        "llm_result",
                        extra={
                            "llm_provider": provider.profile.name,
                            "llm_model": model,
                            "llm_input_tokens": result.usage.input_tokens,
                            "llm_output_tokens": result.usage.output_tokens,
                            "llm_cost": str(cost.total_cost) if cost else None,
                            "llm_currency": cost.currency if cost else None,
                        },
                    )
                    return LLMResponse(
                        **result.model_dump(),
                        provider=provider.profile.name,
                        model=model,
                        deployment_type=provider.profile.deployment_type,
                        latency_ms=max(0, (self._clock() - started) * 1000),
                        attempts=tuple(attempts),
                        request_id=request_id,
                        cost=cost,
                    )
                if failure.retryable and number < self.config.retry.max_attempts:
                    await self._sleep(
                        retry_delay(self.config.retry, number, self._random(), failure.retry_after)
                    )
                    continue
                if failure.retryable or (
                    isinstance(failure, AuthenticationError) and route.fallback_on_authentication
                ):
                    break
                raise failure
        raise GatewayExhaustedError(attempts=tuple(attempts))

    def _record(
        self,
        attempts: list[Attempt],
        profile: ProviderProfile,
        model: str,
        number: int,
        started: float,
        outcome: Literal["success", "error", "cancelled"],
        error: LLMError | None = None,
    ) -> None:
        attempt = Attempt(
            sequence=len(attempts) + 1,
            provider=profile.name,
            model=model,
            deployment_type=profile.deployment_type,
            attempt_number=number,
            outcome=outcome,
            error_category=error.category if error else None,
            http_status=error.http_status if error else None,
            latency_ms=max(0, (self._clock() - started) * 1000),
            retryable=error.retryable if error else False,
        )
        attempts.append(attempt)
        logger.info(
            "llm_attempt",
            extra={
                "llm_provider": profile.name,
                "llm_model": model,
                "llm_deployment": profile.deployment_type.value,
                "llm_sequence": attempt.sequence,
                "llm_attempt": number,
                "llm_outcome": outcome,
                "llm_error": error.category.value if error else None,
                "status_code": attempt.http_status,
                "duration_ms": attempt.latency_ms,
            },
        )

    async def generate_structured[T: BaseModel](
        self, *, request: LLMRequest, response_model: type[T]
    ) -> StructuredResult[T]:
        """Validate generated JSON locally; validation failure never silently regenerates."""
        try:
            schema = TypeAdapter(JsonObject).validate_python(response_model.model_json_schema())
            structured = LLMRequest.model_validate(
                {**request.model_dump(), "structured_schema": schema}
            )
        except (ValidationError, ValueError):
            raise ConfigurationError() from None
        response = await self.generate(structured)
        try:
            if response.message.content is None or response.finish_reason != "stop":
                raise ValueError
            validate_json_syntax(response.message.content)
            value = response_model.model_validate_json(response.message.content, strict=True)
        except (ValidationError, ValueError, RecursionError):
            raise StructuredOutputError(attempts=response.attempts) from None
        return StructuredResult[T](value=value, response=response)

    async def close(self) -> None:
        """Close all owned clients, even if one close operation fails."""
        if not self._closed:
            self._closed = True
            results = await asyncio.gather(
                *(provider.close() for provider in self._providers.values()), return_exceptions=True
            )
            if any(isinstance(result, BaseException) for result in results):
                raise ConfigurationError()
