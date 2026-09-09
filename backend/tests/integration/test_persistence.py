from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.models import Tenant, TicketPriority, TicketStatus
from app.db.session import Database
from app.repositories.tenant import TenantRepository
from app.repositories.ticket import TicketRepository

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def tenant_and_ticket(db: Database) -> tuple[UUID, UUID]:
    async with db.transaction() as session:
        tenant = await TenantRepository(session).create(slug="owner", name="Owner")
        ticket = await TicketRepository(session).create(
            tenant_id=tenant.id, title="Printer offline"
        )
        return tenant.id, ticket.id


async def test_connection_and_migration(
    database: Database, migrated_database: dict[str, str]
) -> None:
    assert await database.ready()
    assert "20260909_02" in migrated_database["current"]
    assert migrated_database["downgrade"] == "passed"
    assert "20260909_02" in migrated_database["reupgrade"]
    assert "No new upgrade operations" in migrated_database["metadata_check"]


async def test_schema_constraints_and_indexes(database: Database) -> None:
    assert database.engine is not None
    async with database.engine.connect() as connection:
        indexes = await connection.run_sync(lambda conn: inspect(conn).get_indexes("tickets"))
        checks = await connection.run_sync(
            lambda conn: inspect(conn).get_check_constraints("tickets")
        )
        foreign_keys = await connection.run_sync(
            lambda conn: inspect(conn).get_foreign_keys("tickets")
        )
    assert {item["name"] for item in indexes} == {
        "ix_tickets_tenant_id",
        "ix_tickets_tenant_id_status",
    }
    assert {item["name"] for item in checks} == {
        "ck_tickets_ticket_status",
        "ck_tickets_ticket_priority",
        "ck_tickets_title_not_blank",
    }
    assert foreign_keys[0]["options"]["ondelete"] == "RESTRICT"


async def test_tenant_create_and_lookup(database: Database) -> None:
    async with database.transaction() as session:
        tenant = await TenantRepository(session).create(slug="tenant-a", name="Tenant A")
        tenant_id = tenant.id
        assert isinstance(tenant_id, UUID)
        assert tenant.created_at.utcoffset() is not None
        assert tenant.updated_at.utcoffset() is not None
    async with database.transaction() as session:
        repo = TenantRepository(session)
        found = await repo.get_by_id(tenant_id=tenant_id)
        by_slug = await repo.get_by_slug(slug="tenant-a")
        assert found is not None and found.name == "Tenant A"
        assert by_slug is not None and by_slug.id == tenant_id
        assert await repo.get_by_id(tenant_id=uuid4()) is None
        assert await repo.get_by_slug(slug="missing") is None


async def test_duplicate_slug_rolls_back_whole_transaction(database: Database) -> None:
    with pytest.raises(IntegrityError):
        async with database.transaction() as session:
            repo = TenantRepository(session)
            await repo.create(slug="duplicate", name="First")
            await repo.create(slug="duplicate", name="Second")
    async with database.transaction() as session:
        assert await TenantRepository(session).get_by_slug(slug="duplicate") is None


async def test_ticket_crud_and_timestamps(database: Database) -> None:
    tenant_id, ticket_id = await tenant_and_ticket(database)
    async with database.transaction() as session:
        repo = TicketRepository(session)
        ticket = await repo.get_by_id(tenant_id=tenant_id, ticket_id=ticket_id)
        assert ticket is not None
        assert ticket.status is TicketStatus.OPEN and ticket.priority is TicketPriority.MEDIUM
        assert ticket.description == ""
        assert ticket.created_at.utcoffset() is not None
        before = ticket.updated_at
        assert await repo.update(tenant_id=tenant_id, ticket_id=ticket_id) is ticket
        changed = await repo.update(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            title="Printer fixed",
            description="Restarted",
            status=TicketStatus.RESOLVED,
            priority=TicketPriority.HIGH,
        )
        assert changed is not None and changed.updated_at > before
        assert changed.title == "Printer fixed" and changed.description == "Restarted"
        assert changed.status is TicketStatus.RESOLVED and changed.priority is TicketPriority.HIGH
    async with database.transaction() as session:
        repo = TicketRepository(session)
        assert len(await repo.list(tenant_id=tenant_id)) == 1
        assert await repo.delete(tenant_id=tenant_id, ticket_id=ticket_id)
        assert await repo.get_by_id(tenant_id=tenant_id, ticket_id=ticket_id) is None
        assert not await repo.delete(tenant_id=tenant_id, ticket_id=ticket_id)


@pytest.mark.parametrize("status", list(TicketStatus))
@pytest.mark.parametrize("priority", list(TicketPriority))
async def test_all_enum_values_round_trip(
    database: Database, status: TicketStatus, priority: TicketPriority
) -> None:
    async with database.transaction() as session:
        tenant = await TenantRepository(session).create(slug="enum", name="Enum")
        ticket = await TicketRepository(session).create(
            tenant_id=tenant.id, title="Issue", status=status, priority=priority
        )
        tenant_id, ticket_id = tenant.id, ticket.id
    async with database.transaction() as session:
        found = await TicketRepository(session).get_by_id(tenant_id=tenant_id, ticket_id=ticket_id)
        assert found is not None and found.status is status and found.priority is priority


@pytest.mark.parametrize("column", ["status", "priority"])
async def test_database_rejects_invalid_enum_even_for_raw_sql(
    database: Database, column: str
) -> None:
    tenant_id, ticket_id = await tenant_and_ticket(database)
    # Column comes solely from the fixed parameter list; values are bound.
    with pytest.raises(IntegrityError):
        async with database.transaction() as session:
            await session.execute(
                text(
                    f"UPDATE tickets SET {column} = :value WHERE id = :id AND tenant_id = :tenant"
                ),
                {"value": "invalid", "id": ticket_id, "tenant": tenant_id},
            )
    assert await database.ready()


async def test_foreign_key_rejects_missing_tenant(database: Database) -> None:
    with pytest.raises(IntegrityError):
        async with database.transaction() as session:
            await TicketRepository(session).create(tenant_id=uuid4(), title="Orphan")


async def test_foreign_key_restricts_tenant_deletion(database: Database) -> None:
    tenant_id, _ = await tenant_and_ticket(database)
    with pytest.raises(IntegrityError):
        async with database.transaction() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))


async def test_business_exception_rolls_back(database: Database) -> None:
    with pytest.raises(RuntimeError, match="abort"):
        async with database.transaction() as session:
            tenant = await TenantRepository(session).create(slug="rollback", name="Rollback")
            await TicketRepository(session).create(tenant_id=tenant.id, title="Should not commit")
            raise RuntimeError("abort")
    async with database.transaction() as session:
        assert await TenantRepository(session).get_by_slug(slug="rollback") is None
        assert await session.scalar(text("SELECT count(*) FROM tickets")) == 0


@pytest.mark.parametrize("operation", ["read", "update", "delete", "list"])
async def test_cross_tenant_access_is_denied(database: Database, operation: str) -> None:
    async with database.transaction() as session:
        tenants = TenantRepository(session)
        a = await tenants.create(slug="tenant-a", name="A")
        b = await tenants.create(slug="tenant-b", name="B")
        own = await TicketRepository(session).create(tenant_id=a.id, title="A ticket")
        target = await TicketRepository(session).create(tenant_id=b.id, title="B private ticket")
        a_id, b_id, own_id, target_id = a.id, b.id, own.id, target.id
    async with database.transaction() as session:
        repo = TicketRepository(session)
        # Load B first to prove a warm identity map cannot bypass the SQL predicate.
        assert await repo.get_by_id(tenant_id=b_id, ticket_id=target_id) is not None
        if operation == "read":
            assert await repo.get_by_id(tenant_id=a_id, ticket_id=target_id) is None
        elif operation == "update":
            assert await repo.update(tenant_id=a_id, ticket_id=target_id, title="stolen") is None
        elif operation == "delete":
            assert not await repo.delete(tenant_id=a_id, ticket_id=target_id)
        else:
            assert [t.id for t in await repo.list(tenant_id=a_id)] == [own_id]
    async with database.transaction() as session:
        intact = await TicketRepository(session).get_by_id(tenant_id=b_id, ticket_id=target_id)
        assert intact is not None and intact.title == "B private ticket"


async def test_listing_filter_and_bounds(database: Database) -> None:
    tenant_id, _ = await tenant_and_ticket(database)
    async with database.transaction() as session:
        repo = TicketRepository(session)
        assert len(await repo.list(tenant_id=tenant_id, status=TicketStatus.OPEN)) == 1
        assert await repo.list(tenant_id=tenant_id, status=TicketStatus.CLOSED) == []
        assert await repo.list(tenant_id=tenant_id, offset=1) == []
        for limit, offset in [(0, 0), (101, 0), (1, -1)]:
            with pytest.raises(ValueError):
                await repo.list(tenant_id=tenant_id, limit=limit, offset=offset)


async def test_server_updates_tenant_timestamp_for_raw_sql(database: Database) -> None:
    tenant_id, _ = await tenant_and_ticket(database)
    async with database.transaction() as session:
        before = await session.scalar(
            text("SELECT updated_at FROM tenants WHERE id=:id"), {"id": tenant_id}
        )
        after = await session.scalar(
            text("UPDATE tenants SET name=:name WHERE id=:id RETURNING updated_at"),
            {"name": "Changed", "id": tenant_id},
        )
        assert after > before
