import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.db.session import Database, database_url
from app.main import create_app


def test_database_password_is_secret_and_url_is_escaped() -> None:
    fixture_value = "synthetic@password:/?#"
    settings = Settings(_env_file=None, database_password=SecretStr(fixture_value))
    assert fixture_value not in repr(settings)
    url = database_url(settings)
    assert url.password == fixture_value
    assert fixture_value not in str(url)


def test_unconfigured_database_has_no_engine() -> None:
    database = Database(Settings(_env_file=None))
    assert database.engine is None and database.sessions is None
    assert not asyncio.run(database.ready())
    asyncio.run(database.close())
    with pytest.raises(RuntimeError, match="not configured"):
        database_url(Settings(_env_file=None))


def test_unconfigured_transaction_fails_deliberately() -> None:
    async def execute() -> None:
        database = Database(Settings(_env_file=None))
        async with database.transaction():
            pytest.fail("Unconfigured transaction must not yield")

    with pytest.raises(RuntimeError, match="not configured"):
        asyncio.run(execute())


def test_missing_password_is_unready_but_alive() -> None:
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json() == {
            "status": "not_ready",
            "dependencies": {"postgresql": "unavailable"},
        }


def test_blank_password_and_invalid_database_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "")
    assert Settings(_env_file=None).database_password is None
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_ready_timeout=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_pool_size=0)
