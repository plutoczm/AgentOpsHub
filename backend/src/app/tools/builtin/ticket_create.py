"""Write tool accepting business fields only, with server-owned tenant and ID."""

from pydantic import Field, field_validator

from app.db.models import TicketPriority
from app.services.tickets import TicketService, TicketView
from app.tools.contracts import Tool
from app.tools.models import ToolEffect, ToolExecutionContext, ToolModel


class TicketCreateInput(ToolModel):
    """Bound private business input and forbid ownership/server-generated fields."""

    title: str = Field(min_length=1, max_length=300, repr=False)
    description: str = Field(default="", max_length=10000, repr=False)
    priority: TicketPriority = TicketPriority.MEDIUM

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str) -> str:
        """Reject whitespace-only titles before entering a transaction."""
        if not value.strip():
            raise ValueError("Title must not be blank")
        return value


def ticket_create_tool(service: TicketService) -> Tool[TicketCreateInput, TicketView]:
    """Bind the write handler; executor policy runs before service invocation."""

    async def handler(arguments: TicketCreateInput, context: ToolExecutionContext) -> TicketView:
        return await service.create(
            tenant_id=context.tenant_id,
            title=arguments.title,
            description=arguments.description,
            priority=arguments.priority,
        )

    return Tool(
        name="ticket_create",
        description="Create a support ticket for the current tenant.",
        input_model=TicketCreateInput,
        output_model=TicketView,
        effect=ToolEffect.WRITE,
        handler=handler,
    )
