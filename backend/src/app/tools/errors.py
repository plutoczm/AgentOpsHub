"""Semantic exceptions with a closed safe-message vocabulary."""

from typing import ClassVar

from app.tools.models import ToolErrorCategory

SAFE_MESSAGES = {
    ToolErrorCategory.NOT_FOUND: "Tool is not registered.",
    ToolErrorCategory.INPUT_VALIDATION: "Tool arguments are invalid.",
    ToolErrorCategory.POLICY: "Write tools are not permitted by execution policy.",
    ToolErrorCategory.TIMEOUT: "Tool execution timed out; completion may be unknown.",
    ToolErrorCategory.EXECUTION: "Tool execution failed.",
    ToolErrorCategory.RESULT_VALIDATION: "Tool returned an invalid result.",
}


class ToolError(Exception):
    """Base tool failure; caller-supplied exception text is never needed."""

    category: ClassVar[ToolErrorCategory] = ToolErrorCategory.EXECUTION

    def __init__(self) -> None:
        """Initialize with the fixed public message for this category."""
        super().__init__(SAFE_MESSAGES[self.category])


class ToolNotFoundError(ToolError):
    """Requested name is not explicitly registered."""

    category = ToolErrorCategory.NOT_FOUND


class ToolInputValidationError(ToolError):
    """Untrusted arguments do not satisfy the declared input contract."""

    category = ToolErrorCategory.INPUT_VALIDATION


class ToolPolicyError(ToolError):
    """Trusted policy forbids this effect."""

    category = ToolErrorCategory.POLICY


class ToolTimeoutError(ToolError):
    """The executor's deadline expired."""

    category = ToolErrorCategory.TIMEOUT


class ToolExecutionError(ToolError):
    """Handler or service execution failed without exposing its exception."""

    category = ToolErrorCategory.EXECUTION


class ToolResultValidationError(ToolError):
    """Handler output could not be validated or serialized."""

    category = ToolErrorCategory.RESULT_VALIDATION
