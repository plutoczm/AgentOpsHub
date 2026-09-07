from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings, load_settings


def test_defaults_without_dotenv() -> None:
    settings = load_settings(None)
    assert settings.port == 8000
    assert settings.host == "127.0.0.1"
    assert settings.environment == "development"


def test_utf8_dotenv_and_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "AGENTOPSHUB_APP_NAME=企业支持\nAGENTOPSHUB_PORT=8123\nPOSTGRES_DB=example\n",
        encoding="utf-8",
    )
    assert load_settings(env).app_name == "企业支持"
    assert load_settings(env).port == 8123
    monkeypatch.setenv("AGENTOPSHUB_PORT", "8124")
    assert load_settings(env).port == 8124
    assert Settings(_env_file=env, port=8125).port == 8125


@pytest.mark.parametrize("port", ["0", "65536", "not-a-number"])
def test_invalid_port_fails_fast(port: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTOPSHUB_PORT", port)
    with pytest.raises(ValidationError):
        load_settings(None)


@pytest.mark.parametrize("field", ["ENVIRONMENT", "LOG_LEVEL"])
def test_invalid_enum_hides_input(field: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(f"AGENTOPSHUB_{field}", "sensitive-invalid-value")
    with pytest.raises(ValidationError) as caught:
        load_settings(None)
    assert "sensitive-invalid-value" not in str(caught.value)


def test_settings_are_immutable() -> None:
    settings = load_settings(None)
    with pytest.raises(ValidationError):
        settings.port = 8123  # type: ignore[misc]  # Exercise runtime frozen validation.
