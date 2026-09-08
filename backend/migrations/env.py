"""Alembic entrypoint using the application's async PostgreSQL configuration."""

import asyncio

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import load_settings
from app.db.base import Base
from app.db.models import Tenant, Ticket  # noqa: F401 -- register all metadata
from app.db.session import database_url
from app.observability.logging import configure_logging


def run_migrations(connection: Connection) -> None:
    """Execute revision operations in Alembic's transaction."""
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    """Open one migration connection and always dispose its engine."""
    engine = create_async_engine(
        database_url(load_settings()), poolclass=pool.NullPool, hide_parameters=True
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


def main() -> None:
    """Run offline SQL generation or online migrations without printing credentials."""
    configure_logging("WARNING")
    if context.is_offline_mode():
        context.configure(
            dialect_name="postgresql", target_metadata=Base.metadata, literal_binds=True
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        try:
            asyncio.run(run_online())
        except Exception:
            raise RuntimeError(
                "Database migration failed; verify configuration and PostgreSQL."
            ) from None


main()
