"""Bounded ticket lookup inside the trusted context's tenant."""

from uuid import UUID

from pydantic import Field

from app.db.models import TicketStatus
from app.services.tickets import TicketService, TicketView
from app.tools.contracts import Tool
from app.tools.models import ToolEffect, ToolExecutionContext, ToolModel


class TicketSearchInput(ToolModel):
    """Expose only focused filters; tenant_id and all unknown fields are forbidden."""

    status: TicketStatus | None = None
    limit: int = Field(default=20, ge=1, le=100)
    ticket_id: UUID | None = None


class TicketSearchOutput(ToolModel):
    """Return bounded ticket summaries, excluding descriptions."""

    tickets: list[TicketView] = Field(max_length=100, repr=False)


def ticket_search_tool(service: TicketService) -> Tool[TicketSearchInput, TicketSearchOutput]:
    """Bind a service without allowing the model to select dependencies or tenant."""

    async def handler(
        arguments: TicketSearchInput,
        context: ToolExecutionContext,
    ) -> TicketSearchOutput:
        return TicketSearchOutput(
            tickets=await service.search(
                tenant_id=context.tenant_id,
                status=arguments.status,
                limit=arguments.limit,
                ticket_id=arguments.ticket_id,
            )
        )

    return Tool(
        name="ticket_search",
        description="List current tenant tickets or look up an exact ID.",
        input_model=TicketSearchInput,
        output_model=TicketSearchOutput,
        effect=ToolEffect.READ_ONLY,
        handler=handler,
    )
