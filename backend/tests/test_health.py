from uuid import UUID

from fastapi.testclient import TestClient

from app import __version__
from app.core.config import Settings
from app.main import create_app


def test_health_contract(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "AgentOpsHub", "version": __version__}
    assert response.headers["content-type"] == "application/json"
    assert UUID(response.headers["x-request-id"]).version == 4


def test_request_ids_are_unique_and_server_owned(client: TestClient) -> None:
    first = client.get("/health", headers={"X-Request-ID": "untrusted-input"})
    second = client.get("/health")
    assert first.headers["x-request-id"] != "untrusted-input"
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


def test_not_found_has_correlation(client: TestClient) -> None:
    response = client.get("/missing")
    assert response.status_code == 404
    assert UUID(response.headers["x-request-id"])


def test_openapi_health_contract(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/health"]["get"]["responses"]["200"]["content"]["application/json"]


def test_factory_configuration_is_isolated() -> None:
    for name in ("First", "Second"):
        with TestClient(create_app(Settings(_env_file=None, app_name=name))) as client:
            assert client.get("/health").json()["service"] == name


def test_production_hides_docs() -> None:
    with TestClient(create_app(Settings(_env_file=None, environment="production"))) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/health").status_code == 200
