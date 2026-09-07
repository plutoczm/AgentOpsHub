"""Validated application settings, loaded explicitly by the application factory."""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Read prefixed environment variables and an optional UTF-8 dotenv file.

    Precedence is explicit constructor values, environment, dotenv, defaults.
    Infrastructure and model settings will be introduced with their adapters.
    """

    model_config = SettingsConfigDict(
        env_prefix="AGENTOPSHUB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    app_name: str = Field(default="AgentOpsHub", min_length=1, max_length=100)
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)


def load_settings(env_file: Path | None = Path(".env")) -> Settings:
    """Load a fresh validated snapshot; pass None for no dotenv file.

    ValidationError intentionally propagates so invalid startup config fails fast.
    Never log its structured errors, which can contain input values.
    """
    return Settings(_env_file=env_file)
