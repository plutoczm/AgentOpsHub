"""Tenant-scoped ticket operations with detached, deliberately small output DTOs."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Ticket, TicketPriority, TicketStatus
from app.db.session import Database
from app.repositories.ticket import TicketRepository


class TicketView(BaseModel):
    """Safe ticket summary; descriptions and tenant identifiers are omitted."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    id: UUID
    title: str = Field(min_length=1, max_length=300, repr=False)
    status: TicketStatus
    priority: TicketPriority

    @classmethod
    def from_ticket(cls, ticket: Ticket) -> "TicketView":
        """Build and validate before leaving a write transaction."""
        return cls(id=ticket.id, title=ticket.title, status=ticket.status, priority=ticket.priority)


class TicketService:
    """Own per-operation sessions; repository methods never commit."""

    def __init__(self, database: Database) -> None:
        """Reuse the application-owned database pool."""
        self.database = database

    async def search(
        self,
        *,
        tenant_id: UUID,
        status: TicketStatus | None,
        limit: int,
        ticket_id: UUID | None,
    ) -> list[TicketView]:
        """Use a non-committing read session and mandatory repository tenant scope."""
        if self.database.sessions is None:
            raise RuntimeError("PostgreSQL is not configured.")
        async with self.database.sessions() as session:
            repo = TicketRepository(session)
            if ticket_id is not None:
                ticket = await repo.get_by_id(tenant_id=tenant_id, ticket_id=ticket_id)
                if ticket is None or (status is not None and ticket.status is not status):
                    return []
                return [TicketView.from_ticket(ticket)]
            return [
                TicketView.from_ticket(ticket)
                for ticket in await repo.list(
                    tenant_id=tenant_id,
                    status=status,
                    limit=limit,
                )
            ]

    async def create(
        self,
        *,
        tenant_id: UUID,
        title: str,
        description: str,
        priority: TicketPriority,
    ) -> TicketView:
        """Flush and validate output before commit; errors/cancellation roll back."""
        async with self.database.transaction() as session:
            ticket = await TicketRepository(session).create(
                tenant_id=tenant_id,
                title=title,
                description=description,
                priority=priority,
            )
            result = TicketView.from_ticket(ticket)
        return result
