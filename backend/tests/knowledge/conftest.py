import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def no_agent_ingestion(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Ingestion must not call an LLM, Agent or ToolExecutor")

    monkeypatch.setattr("app.llm.gateway.LLMGateway.generate", forbidden)
    monkeypatch.setattr("app.agents.runtime.AgentRuntime.run", forbidden)
    monkeypatch.setattr("app.tools.executor.ToolExecutor.execute", forbidden)
