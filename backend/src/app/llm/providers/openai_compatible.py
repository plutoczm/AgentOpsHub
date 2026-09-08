"""One async Chat Completions adapter for configured compatible HTTP endpoints."""

import asyncio
import json
import math
import re
from typing import Literal

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

from app.llm.capabilities import validate_target
from app.llm.config import ProviderProfile
from app.llm.errors import (
    AuthenticationError,
    BadRequestError,
    ConfigurationError,
    LLMError,
    LLMTimeoutError,
    ProviderResponseError,
    ProviderUnavailableError,
    RateLimitError,
)
from app.llm.models import (
    Capabilities,
    Identifier,
    JsonObject,
    LLMRequest,
    Message,
    ProviderResult,
    Role,
    TokenCount,
    ToolCall,
    ToolName,
    Usage,
)
from app.llm.validation import validate_json_syntax


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)


class _Function(_WireModel):
    name: ToolName
    arguments: str = Field(repr=False)


class _Call(_WireModel):
    id: Identifier
    type: Literal["function"]
    function: _Function = Field(repr=False)


class _Message(_WireModel):
    role: Literal["assistant"]
    content: str | None = Field(default=None, repr=False)
    tool_calls: tuple[_Call, ...] | None = Field(default=None, repr=False)


class _Choice(_WireModel):
    message: _Message = Field(repr=False)
    finish_reason: str


class _Usage(_WireModel):
    prompt_tokens: TokenCount | None = None
    completion_tokens: TokenCount | None = None
    total_tokens: TokenCount | None = None


class _Completion(_WireModel):
    id: str | None = None
    choices: tuple[_Choice, ...] = Field(min_length=1, max_length=1, repr=False)
    usage: _Usage | None = None


def _status_error(status: int, retry_after: str | None) -> LLMError:
    delay = None
    if retry_after is not None:
        try:
            parsed = float(retry_after)
            if math.isfinite(parsed) and parsed >= 0:
                delay = parsed
        except ValueError:
            pass
    error: type[LLMError]
    if status in {401, 403}:
        error = AuthenticationError
    elif status == 408:
        error = LLMTimeoutError
    elif status == 429:
        error = RateLimitError
    elif 500 <= status <= 599:
        error = ProviderUnavailableError
    else:
        error = BadRequestError
    return error(http_status=status, retry_after=delay)


def _serialize(request: LLMRequest, model: str, capabilities: Capabilities) -> JsonObject:
    messages: list[JsonValue] = []
    for message in request.messages:
        item: JsonObject = {"role": message.role.value, "content": message.content}
        if message.tool_call_id is not None:
            item["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, allow_nan=False),
                    },
                }
                for call in message.tool_calls
            ]
        messages.append(item)
    payload: JsonObject = {
        "model": model,
        "messages": messages,
        "max_tokens": request.max_output_tokens,
        "stream": False,
    }
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.stop:
        payload["stop"] = list(request.stop)
    if request.tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in request.tools
        ]
    if request.structured_schema is not None:
        if capabilities.structured_output:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_result",
                    "strict": True,
                    "schema": request.structured_schema,
                },
            }
        else:
            payload["response_format"] = {"type": "json_object"}
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": "Return only JSON matching this schema: "
                    + json.dumps(request.structured_schema, allow_nan=False),
                },
            )
    elif request.json_mode:
        payload["response_format"] = {"type": "json_object"}
    # Explicitly reject non-finite/non-JSON protocol data before HTTP serialization.
    json.dumps(payload, allow_nan=False)
    return payload


class OpenAICompatibleProvider:
    """Reuse one pooled client; there is no vendor SDK, retry loop or local subprocess."""

    def __init__(
        self, profile: ProviderProfile, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """Create a client without network I/O; own and close its injected transport."""
        self.profile = profile
        timeout = profile.timeout
        self._client = httpx.AsyncClient(
            base_url=profile.base_url,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(
                connect=timeout.connect, read=timeout.read, write=timeout.write, pool=timeout.pool
            ),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def generate(
        self, request: LLMRequest, model: str, capabilities: Capabilities
    ) -> ProviderResult:
        """Send once with bounded buffering; map failures without retaining headers/body."""
        if self._client.is_closed:
            raise ConfigurationError()
        validate_target(self.profile, request, capabilities)
        headers = {}
        if self.profile.api_key is not None:
            headers["Authorization"] = "Bearer " + self.profile.api_key.get_secret_value()
        try:
            payload = _serialize(request, model, capabilities)
        except (ValueError, TypeError):
            raise BadRequestError() from None
        try:
            async with (
                asyncio.timeout(self.profile.timeout.attempt),
                self._client.stream(
                    "POST", "chat/completions", json=payload, headers=headers
                ) as response,
            ):
                if not 200 <= response.status_code < 300:
                    raise _status_error(response.status_code, response.headers.get("retry-after"))
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self.profile.max_response_bytes:
                        raise ProviderResponseError()
                return self._normalize(bytes(content), response.headers.get("x-request-id"))
        except (httpx.TimeoutException, TimeoutError):
            raise LLMTimeoutError() from None
        except httpx.DecodingError:
            raise ProviderResponseError() from None
        except httpx.TransportError:
            raise ProviderUnavailableError() from None

    def _normalize(self, content: bytes, header_id: str | None) -> ProviderResult:
        try:
            validate_json_syntax(content)
            wire = _Completion.model_validate_json(content)
            for call in wire.choices[0].message.tool_calls or ():
                validate_json_syntax(call.function.arguments)
            choice = wire.choices[0]
            calls = tuple(
                ToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=TypeAdapter(JsonObject).validate_json(call.function.arguments),
                )
                for call in choice.message.tool_calls or ()
            )
            usage = (
                Usage()
                if wire.usage is None
                else Usage(
                    input_tokens=wire.usage.prompt_tokens,
                    output_tokens=wire.usage.completion_tokens,
                    total_tokens=wire.usage.total_tokens,
                )
            )
            request_id = header_id or wire.id
            if request_id is not None and (
                not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id)
                or self.profile.api_key is not None
                and self.profile.api_key.get_secret_value() in request_id
            ):
                request_id = None
            finish: Literal["stop", "length", "tool_calls", "content_filter", "other"] = "other"
            match choice.finish_reason:
                case "stop":
                    finish = "stop"
                case "length":
                    finish = "length"
                case "tool_calls":
                    finish = "tool_calls"
                case "content_filter":
                    finish = "content_filter"
            return ProviderResult(
                message=Message(
                    role=Role.ASSISTANT, content=choice.message.content, tool_calls=calls
                ),
                finish_reason=finish,
                usage=usage,
                provider_request_id=request_id,
            )
        except (ValidationError, ValueError, TypeError, RecursionError):
            raise ProviderResponseError() from None

    async def close(self) -> None:
        """Release the reusable client's connection pool."""
        await self._client.aclose()
