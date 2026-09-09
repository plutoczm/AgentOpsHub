import socket
import urllib.request

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def offline_deterministic_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Retrieval/evaluation must not invoke an Agent, LLM, tool or external network")

    monkeypatch.setattr("app.llm.gateway.LLMGateway.generate", forbidden)
    monkeypatch.setattr("app.agents.runtime.AgentRuntime.run", forbidden)
    monkeypatch.setattr("app.tools.executor.ToolExecutor.execute", forbidden)
    monkeypatch.setattr("httpx2.AsyncClient.send", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
