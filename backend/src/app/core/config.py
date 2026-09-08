"""Validated application settings, loaded explicitly by the application factory."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.llm.config import GatewayConfig


class Settings(BaseSettings):
    """Read prefixed environment variables and an optional UTF-8 dotenv file.

    Precedence is explicit constructor values, environment, dotenv, defaults.
    POSTGRES_* aliases are shared with Compose; secrets never appear in repr.
    """

    model_config = SettingsConfigDict(
        env_prefix="AGENTOPSHUB_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    llm: GatewayConfig = Field(default_factory=GatewayConfig)

    app_name: str = Field(default="AgentOpsHub", min_length=1, max_length=100)
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)
    database_host: str = Field(default="127.0.0.1", validation_alias="POSTGRES_HOST", min_length=1)
    database_port: int = Field(default=5432, validation_alias="POSTGRES_PORT", ge=1, le=65535)
    database_name: str = Field(default="agentopshub", validation_alias="POSTGRES_DB", min_length=1)
    database_user: str = Field(
        default="agentopshub", validation_alias="POSTGRES_USER", min_length=1
    )
    database_password: SecretStr | None = Field(default=None, validation_alias="POSTGRES_PASSWORD")
    database_pool_size: int = Field(default=5, ge=1, le=20)
    database_connect_timeout: float = Field(default=3.0, gt=0, le=30)
    database_command_timeout: float = Field(default=5.0, gt=0, le=60)
    database_ready_timeout: float = Field(default=2.0, gt=0, le=10)

    @field_validator("database_password", mode="before")
    @classmethod
    def empty_password_is_unconfigured(cls, value: object) -> object:
        """Allow an empty example environment while keeping liveness independent."""
        return None if value == "" else value


def load_settings(env_file: Path | None = Path(".env")) -> Settings:
    """Load a fresh validated snapshot; pass None for no dotenv file.

    ValidationError intentionally propagates so invalid startup config fails fast.
    Never log its structured errors, which can contain input values.
    """
    return Settings(_env_file=env_file)
