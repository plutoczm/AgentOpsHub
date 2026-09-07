import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.observability.logging import JsonFormatter, configure_logging, request_id_context


def test_formatter_excludes_sensitive_messages_and_extras() -> None:
    record = logging.LogRecord(
        "vendor", logging.ERROR, __file__, 1, "secret=%s", ("top-secret",), None
    )
    record.api_key = "top-secret"
    formatted = JsonFormatter().format(record)
    assert "top-secret" not in formatted
    assert json.loads(formatted)["event"] == "log"


def test_logging_setup_does_not_duplicate_handlers(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    configure_logging("INFO")
    logging.getLogger("app.test").info("application_started")
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "application_started"


def test_request_logs_safe_metadata(capsys: pytest.CaptureFixture[str]) -> None:
    with TestClient(create_app(Settings(_env_file=None))) as client:
        response = client.get(
            "/health?api_key=top-secret", headers={"Authorization": "Bearer top-secret"}
        )
        assert request_id_context.get() is None
    output = capsys.readouterr().out
    assert "top-secret" not in output
    events = [json.loads(line) for line in output.splitlines()]
    event = next(item for item in events if item["event"] == "request_completed")
    assert event["request_id"] == response.headers["x-request-id"]
    assert event["route"] == "/health"
    assert event["status_code"] == 200
    assert event["duration_ms"] >= 0


def test_unhandled_error_is_safe_and_correlated(capsys: pytest.CaptureFixture[str]) -> None:
    app = create_app(Settings(_env_file=None))

    @app.get("/failure")
    async def fail() -> None:
        raise RuntimeError("top-secret exception payload")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/failure")
        assert request_id_context.get() is None
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Internal server error",
        "request_id": response.headers["x-request-id"],
    }
    output = capsys.readouterr().out
    assert "top-secret" not in output + response.text
    events = [json.loads(line) for line in output.splitlines()]
    error = next(item for item in events if item["event"] == "unhandled_exception")
    completed = next(item for item in events if item["event"] == "request_completed")
    assert error["error_type"] == "RuntimeError"
    assert completed["status_code"] == 500
    assert error["request_id"] == completed["request_id"] == response.headers["x-request-id"]
