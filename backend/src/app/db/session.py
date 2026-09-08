"""Application-owned engine, bounded readiness and transaction boundaries."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import URL, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

logger = logging.getLogger(__name__)


def database_url(settings: Settings) -> URL:
    """Build an escaped driver URL without logging or embedding credentials in source."""
    if settings.database_password is None:
        raise RuntimeError("PostgreSQL is not configured: supply POSTGRES_PASSWORD.")
    return URL.create(
        "postgresql+asyncpg",
        username=settings.database_user,
        password=settings.database_password.get_secret_value(),
        host=settings.database_host,
        port=settings.database_port,
        database=settings.database_name,
    )


class Database:
    """Own one lazily connecting engine and session factory for an application lifespan."""

    def __init__(self, settings: Settings) -> None:
        """Create the pool without establishing a database connection."""
        self.ready_timeout = settings.database_ready_timeout
        self.engine: AsyncEngine | None = None
        self.sessions: async_sessionmaker[AsyncSession] | None = None
        if settings.database_password is not None:
            self.engine = create_async_engine(
                database_url(settings),
                pool_pre_ping=True,
                pool_size=settings.database_pool_size,
                max_overflow=0,
                pool_timeout=settings.database_connect_timeout,
                connect_args={
                    "timeout": settings.database_connect_timeout,
                    "command_timeout": settings.database_command_timeout,
                },
                hide_parameters=True,
                echo=False,
            )
            self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def ready(self) -> bool:
        """Probe PostgreSQL within a total deadline; do not expose driver exceptions."""
        if self.engine is None:
            return False
        try:
            async with asyncio.timeout(self.ready_timeout), self.engine.connect() as connection:
                return bool(await connection.scalar(text("SELECT 1")) == 1)
        except Exception as exc:
            # Driver connection errors are not all wrapped by SQLAlchemy. This is
            # the I/O boundary; cancellation (BaseException) deliberately propagates.
            logger.warning("database_unavailable", extra={"error_type": type(exc).__name__})
            return False

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Commit on success, roll back on any exception, then close the session.

        Integrity/driver failures propagate to callers; repositories never commit.
        Do not share a session between concurrent tasks.
        """
        if self.sessions is None:
            raise RuntimeError("PostgreSQL is not configured.")
        try:
            async with self.sessions.begin() as session:
                yield session
        except SQLAlchemyError as exc:
            logger.warning("transaction_failed", extra={"error_type": type(exc).__name__})
            raise

    async def close(self) -> None:
        """Release pooled connections during application shutdown."""
        if self.engine is not None:
            await self.engine.dispose()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Provide a transaction; use the function-scoped dependency alias below."""
    database: Database = request.app.state.database
    async with database.transaction() as session:
        yield session


TransactionSession = Annotated[AsyncSession, Depends(get_session, scope="function")]
