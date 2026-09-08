"""Tenant persistence model."""

from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps


class Tenant(Timestamps, Base):
    """Represent an organization owning tickets; identity is not authentication."""

    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint("length(trim(slug)) > 0", name="slug_not_blank"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
