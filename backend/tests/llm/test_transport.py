import json

import httpx2 as httpx
import pytest

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
from app.llm.models import DeploymentType, Message, Role, ToolCall, ToolDefinition
from app.llm.providers.openai_compatible import OpenAICompatibleProvider

from .helpers import ALL_CAPABILITIES, FAKE_KEY, completion, profile, request

pytestmark = pytest.mark.anyio


async def test_real_serialization_and_success_normalization() -> None:
    seen: list[httpx.Request] = []

    def handle(sent: httpx.Request) -> httpx.Response:
        seen.append(sent)
        assert sent.url == "https://primary.example.invalid/v1/chat/completions"
        assert sent.headers["Authorization"] == "Bearer " + FAKE_KEY
        body = json.loads(sent.content)
        assert body["model"] == "explicit-model" and body["stream"] is False
        assert body["max_tokens"] == 100 and body["temperature"] == 0.2 and body["stop"] == ["END"]
        assert body["messages"][0] == {"role": "user", "content": "PRIVATE TEST PROMPT"}
        assert sent.extensions["timeout"] == {"connect": 5, "read": 30, "write": 10, "pool": 5}
        return httpx.Response(
            200,
            json=completion(usage={"prompt_tokens": 12, "completion_tokens": 4}),
            headers={"x-request-id": "provider-id-1"},
        )

    provider = OpenAICompatibleProvider(profile(), transport=httpx.MockTransport(handle))
    try:
        call = request().model_copy(
            update={"max_output_tokens": 100, "temperature": 0.2, "stop": ("END",)}
        )
        result = await provider.generate(call, "explicit-model", ALL_CAPABILITIES)
        assert result.message.content == "test answer" and result.finish_reason == "stop"
        assert result.usage.total_tokens == 16 and result.provider_request_id == "provider-id-1"
        await provider.generate(call, "explicit-model", ALL_CAPABILITIES)
        assert len(seen) == 2
    finally:
        await provider.close()


@pytest.mark.parametrize(
    "deployment,url",
    [
        (DeploymentType.LOCAL, "http://127.0.0.1:11434/v1/"),
        (DeploymentType.PRIVATE, "http://private-model.internal:8080/v1/"),
    ],
)
async def test_local_private_transport_without_fake_auth(
    deployment: DeploymentType, url: str
) -> None:
    def handle(sent: httpx.Request) -> httpx.Response:
        assert str(sent.url) == url + "chat/completions"
        assert "authorization" not in sent.headers
        return httpx.Response(200, json=completion())

    configured = profile(authenticated=False, deployment=deployment).model_copy(
        update={"base_url": url}
    )
    provider = OpenAICompatibleProvider(configured, transport=httpx.MockTransport(handle))
    try:
        result = await provider.generate(request(), "test-model", ALL_CAPABILITIES)
        assert result.usage.total_tokens is None
    finally:
        await provider.close()


@pytest.mark.parametrize(
    "status,error,retryable",
    [
        (400, BadRequestError, False),
        (401, AuthenticationError, False),
        (403, AuthenticationError, False),
        (404, BadRequestError, False),
        (409, BadRequestError, False),
        (422, BadRequestError, False),
        (408, LLMTimeoutError, True),
        (429, RateLimitError, True),
        (500, ProviderUnavailableError, True),
        (502, ProviderUnavailableError, True),
        (503, ProviderUnavailableError, True),
        (302, BadRequestError, False),
    ],
)
async def test_status_matrix_and_sanitized_errors(
    status: int, error: type[LLMError], retryable: bool
) -> None:
    provider = OpenAICompatibleProvider(
        profile(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                status,
                text="Authorization: Bearer " + FAKE_KEY,
                headers={"retry-after": "2", "location": "https://elsewhere.invalid"},
            )
        ),
    )
    try:
        with pytest.raises(error) as caught:
            await provider.generate(request(), "test-model", ALL_CAPABILITIES)
        assert caught.value.retryable is retryable and caught.value.http_status == status
        assert FAKE_KEY not in str(caught.value) + repr(caught.value)
        assert "Authorization" not in str(caught.value)
    finally:
        await provider.close()


@pytest.mark.parametrize(
    "exception", [httpx.ConnectError, httpx.ReadError, httpx.ReadTimeout, httpx.PoolTimeout]
)
async def test_transport_failures_are_normalized(exception: type[httpx.TransportError]) -> None:
    def handle(sent: httpx.Request) -> httpx.Response:
        raise exception("Authorization: Bearer " + FAKE_KEY, request=sent)

    provider = OpenAICompatibleProvider(profile(), transport=httpx.MockTransport(handle))
    try:
        expected = (
            LLMTimeoutError
            if issubclass(exception, httpx.TimeoutException)
            else ProviderUnavailableError
        )
        with pytest.raises(expected) as caught:
            await provider.generate(request(), "test-model", ALL_CAPABILITIES)
        assert FAKE_KEY not in str(caught.value) + repr(caught.value)
        assert caught.value.__suppress_context__
    finally:
        await provider.close()


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"{}",
        b'{"choices":[]}',
        b'{"choices":[{"message":{"role":"user","content":"x"},"finish_reason":"stop"}]}',
    ],
)
async def test_bad_provider_payload(body: bytes) -> None:
    provider = OpenAICompatibleProvider(
        profile(), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))
    )
    try:
        with pytest.raises(ProviderResponseError):
            await provider.generate(request(), "test-model", ALL_CAPABILITIES)
    finally:
        await provider.close()


async def test_response_buffer_limit() -> None:
    configured = profile().model_copy(update={"max_response_bytes": 1024})
    provider = OpenAICompatibleProvider(
        configured, transport=httpx.MockTransport(lambda _: httpx.Response(200, text="x" * 2048))
    )
    try:
        with pytest.raises(ProviderResponseError):
            await provider.generate(request(), "test-model", ALL_CAPABILITIES)
    finally:
        await provider.close()


async def test_missing_required_key_never_sends() -> None:
    def handle(sent: httpx.Request) -> httpx.Response:
        pytest.fail("Missing key must fail before transport")

    provider = OpenAICompatibleProvider(
        profile().model_copy(update={"api_key": None}), transport=httpx.MockTransport(handle)
    )
    try:
        with pytest.raises(ConfigurationError):
            await provider.generate(request(), "test-model", ALL_CAPABILITIES)
    finally:
        await provider.close()


@pytest.mark.parametrize("arguments", ['{"hostname":"printer"}', "not-json", "[]"])
async def test_tool_protocol_serialization_and_parsing(arguments: str) -> None:
    tool = ToolDefinition(
        name="lookup", description="Synthetic declaration", parameters={"type": "object"}
    )
    call = ToolCall(id="old-call", name="lookup", arguments={"hostname": "old"})
    req = request().model_copy(
        update={
            "tools": (tool,),
            "messages": (
                Message(role=Role.ASSISTANT, tool_calls=(call,)),
                Message(role=Role.TOOL, content="previous result", tool_call_id="old-call"),
            ),
        }
    )

    def handle(sent: httpx.Request) -> httpx.Response:
        body = json.loads(sent.content)
        assert body["tools"][0]["function"]["parameters"] == {"type": "object"}
        assert (
            json.loads(body["messages"][0]["tool_calls"][0]["function"]["arguments"])
            == call.arguments
        )
        assert body["messages"][1]["tool_call_id"] == "old-call"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "id": "new-call",
                                    "function": {"name": "lookup", "arguments": arguments},
                                }
                            ],
                        },
                    }
                ]
            },
        )

    provider = OpenAICompatibleProvider(profile(), transport=httpx.MockTransport(handle))
    try:
        if arguments.startswith("{"):
            result = await provider.generate(req, "test-model", ALL_CAPABILITIES)
            assert result.message.tool_calls[0].arguments == {"hostname": "printer"}
        else:
            with pytest.raises(ProviderResponseError):
                await provider.generate(req, "test-model", ALL_CAPABILITIES)
    finally:
        await provider.close()


@pytest.mark.parametrize("request_id", [FAKE_KEY, "invalid request ID"])
async def test_untrusted_provider_ids_are_not_exposed(request_id: str) -> None:
    provider = OpenAICompatibleProvider(
        profile(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=completion(), headers={"x-request-id": request_id})
        ),
    )
    try:
        assert (
            await provider.generate(request(), "test-model", ALL_CAPABILITIES)
        ).provider_request_id is None
    finally:
        await provider.close()


async def test_invalid_content_encoding_is_a_safe_response_error() -> None:
    provider = OpenAICompatibleProvider(
        profile(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, content=b"invalid gzip", headers={"content-encoding": "gzip"}
            )
        ),
    )
    try:
        with pytest.raises(ProviderResponseError):
            await provider.generate(request(), "test-model", ALL_CAPABILITIES)
    finally:
        await provider.close()
