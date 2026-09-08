"""Deterministic PostgreSQL readiness response without infrastructure details."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ReadinessDependencies(BaseModel):
    """Expose only PostgreSQL, the sole Phase 1 runtime dependency."""

    postgresql: Literal["ok", "unavailable"]


class ReadinessResponse(BaseModel):
    """Distinguish liveness from database connectivity."""

    model_config = ConfigDict(frozen=True)
    status: Literal["ready", "not_ready"]
    dependencies: ReadinessDependencies
