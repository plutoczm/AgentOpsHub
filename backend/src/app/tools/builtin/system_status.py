"""Small, read-only dependency status through an injected async checker."""

from collections.abc import Awaitable, Callable
from typing import Literal

from app.tools.contracts import Tool
from app.tools.models import ToolEffect, ToolExecutionContext, ToolModel


class SystemStatusInput(ToolModel):
    """Accept no model-selected services, URLs or machine commands."""


class SystemStatusOutput(ToolModel):
    """Report only application and PostgreSQL availability."""

    application: Literal["ok"] = "ok"
    postgresql: Literal["ok", "unavailable"]


def system_status_tool(
    check: Callable[[], Awaitable[bool]],
) -> Tool[SystemStatusInput, SystemStatusOutput]:
    """Build a tool independent of the HTTP readiness endpoint."""

    async def handler(
        arguments: SystemStatusInput,
        context: ToolExecutionContext,
    ) -> SystemStatusOutput:
        return SystemStatusOutput(postgresql="ok" if await check() else "unavailable")

    return Tool(
        name="system_status",
        description="Check application and PostgreSQL availability.",
        input_model=SystemStatusInput,
        output_model=SystemStatusOutput,
        effect=ToolEffect.READ_ONLY,
        handler=handler,
        timeout_seconds=5,
    )
