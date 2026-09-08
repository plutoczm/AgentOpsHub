"""Tenant queries participating in a caller-owned transaction."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tenant


class TenantRepository:
    """Persist tenant records without owning commit/rollback or HTTP behavior."""

    def __init__(self, session: AsyncSession) -> None:
        """Use the session supplied by the transaction boundary."""
        self.session = session

    async def create(self, *, slug: str, name: str) -> Tenant:
        """Flush a new tenant; uniqueness/constraint errors propagate to the boundary."""
        tenant = Tenant(slug=slug, name=name)
        self.session.add(tenant)
        await self.session.flush()
        return tenant

    async def get_by_id(self, *, tenant_id: UUID) -> Tenant | None:
        """Retrieve a tenant by its UUID."""
        return (
            await self.session.scalars(select(Tenant).where(Tenant.id == tenant_id))
        ).one_or_none()

    async def get_by_slug(self, *, slug: str) -> Tenant | None:
        """Retrieve a tenant by its unique slug."""
        return (await self.session.scalars(select(Tenant).where(Tenant.slug == slug))).one_or_none()
