"""Typed async tools and the minimal heterogeneous registry interface."""

import json
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from app.llm.models import ToolDefinition
from app.tools.errors import ToolInputValidationError, ToolResultValidationError
from app.tools.models import ToolEffect, ToolExecutionContext, ToolModel

JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


class RegisteredTool(Protocol):
    """Read-only execution surface consumed by registry and executor."""

    @property
    def name(self) -> str:
        """Return a validated deterministic name."""
        ...

    @property
    def effect(self) -> ToolEffect:
        """Return the declared side effect."""
        ...

    @property
    def timeout_seconds(self) -> float:
        """Return a finite execution budget."""
        ...

    def llm_definition(self) -> ToolDefinition:
        """Derive the existing LLM schema representation."""
        ...

    def validate_input(self, arguments: object) -> BaseModel:
        """Validate untrusted arguments before execution."""
        ...

    async def invoke(self, arguments: BaseModel, context: ToolExecutionContext) -> BaseModel:
        """Invoke the typed async handler with validated input and trusted context."""
        ...

    def validate_output(self, value: BaseModel) -> dict[str, JsonValue]:
        """Validate and serialize only the declared output."""
        ...


@dataclass(frozen=True, kw_only=True)
class Tool[Input: ToolModel, Output: BaseModel]:
    """Bind trusted code to typed models; names match [a-z][a-z0-9_]{0,63}."""

    name: str
    description: str
    input_model: type[Input]
    output_model: type[Output]
    effect: ToolEffect
    handler: Callable[[Input, ToolExecutionContext], Awaitable[Output]] = field(repr=False)
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        """Reject unsafe names, invalid effects and unbounded deadlines at setup."""
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name) is None:
            raise ValueError("Tool name must match [a-z][a-z0-9_]{0,63}")
        if not isinstance(self.effect, ToolEffect):
            raise ValueError("Tool effect must be explicit")
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 300:
            raise ValueError("Tool timeout must be finite and within (0, 300] seconds")

    def llm_definition(self) -> ToolDefinition:
        """Generate JSON Schema directly from the Pydantic input model."""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=JSON_OBJECT.validate_python(self.input_model.model_json_schema()),
        )

    def validate_input(self, arguments: object) -> Input:
        """Use strict JSON validation, allowing JSON UUID/enum strings without coercion."""
        try:
            if not isinstance(arguments, dict):
                raise ToolInputValidationError()
            encoded = json.dumps(arguments, allow_nan=False)
            return self.input_model.model_validate_json(encoded, strict=True)
        except (ValidationError, ValueError, TypeError):
            raise ToolInputValidationError() from None

    async def invoke(self, arguments: BaseModel, context: ToolExecutionContext) -> Output:
        """Restore the generic input type without forwarding arbitrary dictionaries."""
        validated = self.input_model.model_validate(arguments)
        return await self.handler(validated, context)

    def validate_output(self, value: BaseModel) -> dict[str, JsonValue]:
        """Revalidate instances and reject invalid or non-JSON output safely."""
        try:
            if not isinstance(value, self.output_model):
                raise ToolResultValidationError()
            validated = self.output_model.model_validate_json(
                value.model_dump_json(),
                strict=True,
            )
            return JSON_OBJECT.validate_json(validated.model_dump_json(), strict=True)
        except ToolResultValidationError:
            raise
        except Exception:
            raise ToolResultValidationError() from None
