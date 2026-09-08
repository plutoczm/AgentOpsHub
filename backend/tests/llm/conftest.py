import httpx2 as httpx
import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def forbid_real_model_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def blocked(self: object, request: httpx.Request) -> httpx.Response:
        raise AssertionError("LLM tests must inject MockTransport")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
