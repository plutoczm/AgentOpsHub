"""Application execution contracts, separate from LLM wire models."""

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class ToolModel(BaseModel):
    """Strict, immutable payloads; reject unknown fields and hide validation inputs."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class ToolEffect(StrEnum):
    """Small side-effect vocabulary for application-owned execution policy."""

    READ_ONLY = "read_only"
    WRITE = "write"


class ToolErrorCategory(StrEnum):
    """Stable control-flow categories without internal exception payloads."""

    NOT_FOUND = "not_found"
    INPUT_VALIDATION = "input_validation"
    POLICY = "policy"
    TIMEOUT = "timeout"
    EXECUTION = "execution"
    RESULT_VALIDATION = "result_validation"


class ToolExecutionPolicy(ToolModel):
    """Trusted application permission; writes are denied by default."""

    allow_writes: bool = False


class ToolExecutionContext(ToolModel):
    """Trusted tenant identity and optional server-selected knowledge namespace."""

    tenant_id: UUID
    request_id: UUID | None = None
    knowledge_namespace: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
    )
    policy: ToolExecutionPolicy = Field(default_factory=ToolExecutionPolicy)


class ToolFailure(ToolModel):
    """Only a semantic category and fixed safe message cross the boundary."""

    category: ToolErrorCategory
    message: str


class ToolResult(ToolModel):
    """One in-memory execution outcome; payloads are excluded from repr."""

    tool_call_id: str
    tool_name: str
    success: bool
    data: dict[str, JsonValue] | None = Field(default=None, repr=False)
    error: ToolFailure | None = None
    duration_ms: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def outcome(self) -> Self:
        """Prevent contradictory success/error representations."""
        if self.success:
            if self.data is None or self.error is not None:
                raise ValueError("Success requires data and no error")
        elif self.error is None or self.data is not None:
            raise ValueError("Failure requires error and no data")
        return self
