"""Ticket queries with mandatory tenant predicates on every access."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Ticket, TicketPriority, TicketStatus


class TicketRepository:
    """Offer tenant-scoped persistence APIs; no unscoped get/update/delete exists."""

    def __init__(self, session: AsyncSession) -> None:
        """Use a caller-owned session and transaction."""
        self.session = session

    async def create(
        self,
        *,
        tenant_id: UUID,
        title: str,
        description: str = "",
        status: TicketStatus = TicketStatus.OPEN,
        priority: TicketPriority = TicketPriority.MEDIUM,
    ) -> Ticket:
        """Flush a ticket with explicit ownership; the FK verifies its tenant exists."""
        ticket = Ticket(
            tenant_id=tenant_id,
            title=title,
            description=description,
            status=status,
            priority=priority,
        )
        self.session.add(ticket)
        await self.session.flush()
        return ticket

    async def get_by_id(self, *, tenant_id: UUID, ticket_id: UUID) -> Ticket | None:
        """Select by both tenant and ticket UUID, including for identity-map hits."""
        result = await self.session.scalars(
            select(Ticket).where(Ticket.tenant_id == tenant_id, Ticket.id == ticket_id)
        )
        return result.one_or_none()

    async def list(
        self,
        *,
        tenant_id: UUID,
        status: TicketStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Ticket]:
        """List only this tenant's tickets, with bounded, stable pagination."""
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("limit must be 1..100 and offset must be non-negative")
        query = select(Ticket).where(Ticket.tenant_id == tenant_id)
        if status is not None:
            query = query.where(Ticket.status == status)
        query = query.order_by(Ticket.created_at, Ticket.id).limit(limit).offset(offset)
        return (await self.session.scalars(query)).all()

    async def update(
        self,
        *,
        tenant_id: UUID,
        ticket_id: UUID,
        title: str | None = None,
        description: str | None = None,
        status: TicketStatus | None = None,
        priority: TicketPriority | None = None,
    ) -> Ticket | None:
        """Update only the matching tenant row; None means leave a field unchanged."""
        values = {
            key: value
            for key, value in {
                "title": title,
                "description": description,
                "status": status,
                "priority": priority,
            }.items()
            if value is not None
        }
        if not values:
            return await self.get_by_id(tenant_id=tenant_id, ticket_id=ticket_id)
        statement = (
            update(Ticket)
            .where(Ticket.tenant_id == tenant_id, Ticket.id == ticket_id)
            .values(**values)
            .returning(Ticket)
            .execution_options(populate_existing=True)
        )
        return (await self.session.scalars(statement)).one_or_none()

    async def delete(self, *, tenant_id: UUID, ticket_id: UUID) -> bool:
        """Delete only the matching tenant row, returning whether one existed."""
        deleted = await self.session.scalar(
            delete(Ticket)
            .where(Ticket.tenant_id == tenant_id, Ticket.id == ticket_id)
            .returning(Ticket.id)
        )
        return deleted is not None
