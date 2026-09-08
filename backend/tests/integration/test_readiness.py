import socket

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Settings
from app.db.session import TransactionSession
from app.main import create_app

pytestmark = pytest.mark.integration


def test_ready_uses_real_postgresql(
    postgres_settings: Settings, migrated_database: dict[str, str]
) -> None:
    with TestClient(create_app(postgres_settings)) as client:
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready", "dependencies": {"postgresql": "ok"}}
        assert response.headers["x-request-id"]
        assert client.get("/health").status_code == 200


def test_ready_failure_does_not_break_liveness(postgres_settings: Settings) -> None:
    # Reserve an unused port with no listening database, guaranteeing connection failure.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        settings = postgres_settings.model_copy(update={"database_port": reserved.getsockname()[1]})
        with TestClient(create_app(settings)) as client:
            response = client.get("/ready")
            assert response.status_code == 503
            assert response.json() == {
                "status": "not_ready",
                "dependencies": {"postgresql": "unavailable"},
            }
            assert client.get("/health").status_code == 200
            assert settings.database_password is not None
            assert settings.database_password.get_secret_value() not in response.text


def test_request_transaction_dependency(
    postgres_settings: Settings, migrated_database: dict[str, str]
) -> None:
    app = create_app(postgres_settings)

    @app.get("/test-session")
    async def session_probe(session: TransactionSession) -> dict[str, bool]:
        return {"connected": await session.scalar(text("SELECT 1")) == 1}

    with TestClient(app) as client:
        assert client.get("/test-session").json() == {"connected": True}
