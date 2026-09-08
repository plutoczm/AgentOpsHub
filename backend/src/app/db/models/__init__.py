"""Register all implemented models for Alembic metadata discovery."""

from app.db.models.tenant import Tenant
from app.db.models.ticket import Ticket, TicketPriority, TicketStatus

__all__ = ["Tenant", "Ticket", "TicketPriority", "TicketStatus"]
