from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for name in os.environ:
        if name.startswith("AGENTOPSHUB_"):
            monkeypatch.delenv(name)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(Settings(_env_file=None, environment="test"))
    with TestClient(app) as test_client:
        yield test_client
