"""Explicit in-memory registration; no discovery, imports or execution."""

from pydantic import BaseModel

from app.llm.models import ToolDefinition
from app.tools.contracts import RegisteredTool, Tool
from app.tools.errors import ToolNotFoundError
from app.tools.models import ToolModel


class ToolRegistry:
    """Index immutable tools and export deterministic declarations."""

    def __init__(self) -> None:
        """Start with no tools; composition must explicitly register each one."""
        self._tools: dict[str, RegisteredTool] = {}

    def register[I: ToolModel, O: BaseModel](self, tool: Tool[I, O]) -> None:
        """Reject duplicate names instead of replacing an existing handler."""
        if tool.name in self._tools:
            raise ValueError("Tool name is already registered")
        self._tools[tool.name] = tool

    def lookup(self, name: str) -> RegisteredTool:
        """Resolve an exact registered name or raise a safe typed error."""
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError() from None

    def list_tools(self) -> tuple[RegisteredTool, ...]:
        """Return a stable name-sorted immutable snapshot."""
        return tuple(self._tools[name] for name in sorted(self._tools))

    def llm_definitions(self) -> tuple[ToolDefinition, ...]:
        """Export existing Phase 2 protocol objects, never executable code."""
        return tuple(tool.llm_definition() for tool in self.list_tools())
