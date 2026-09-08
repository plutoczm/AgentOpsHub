"""Tenant-owned support tickets with constrained string enums."""

from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps


class TicketStatus(StrEnum):
    """Allowed ticket lifecycle states, stored by value."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(StrEnum):
    """Allowed ticket urgency levels, stored by value."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Ticket(Timestamps, Base):
    """Store ticket data with tenant ownership enforced by a foreign key."""

    __tablename__ = "tickets"
    __table_args__ = (
        Index("ix_tickets_tenant_id", "tenant_id"),
        Index("ix_tickets_tenant_id_status", "tenant_id", "status"),
        CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    status: Mapped[TicketStatus] = mapped_column(
        Enum(
            TicketStatus,
            name="ticket_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        default=TicketStatus.OPEN,
        server_default="open",
        nullable=False,
    )
    priority: Mapped[TicketPriority] = mapped_column(
        Enum(
            TicketPriority,
            name="ticket_priority",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        default=TicketPriority.MEDIUM,
        server_default="medium",
        nullable=False,
    )
