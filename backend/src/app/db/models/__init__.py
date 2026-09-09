"""Register all implemented models for Alembic metadata discovery."""

from app.db.models.knowledge import DocumentRevision, KnowledgeChunk, KnowledgeDocument
from app.db.models.tenant import Tenant
from app.db.models.ticket import Ticket, TicketPriority, TicketStatus

__all__ = [
    "DocumentRevision",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "Tenant",
    "Ticket",
    "TicketPriority",
    "TicketStatus",
]
