import asyncio
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, text

from app.core.config import Settings
from app.db.models import DocumentRevision, KnowledgeChunk, KnowledgeDocument, Tenant, Ticket
from app.db.session import Database

ROOT = Path(__file__).resolve().parents[3]
REVISION = "20260909_02"


@pytest.fixture(scope="session")
def postgres_settings() -> Settings:
    run_id = os.environ.get("AGENTOPSHUB_TEST_RUN_ID", "")
    if not run_id:
        pytest.skip("Use python scripts/dev.py test-integration for isolated PostgreSQL")
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        pytest.fail("Invalid isolated test run ID")
    # Never consume the application's ordinary database settings as a test target.
    name = os.environ["TEST_POSTGRES_DB"]
    if name != f"agentopshub_test_{run_id}":
        pytest.fail("Test database is not owned by this test run")
    return Settings(
        _env_file=None,
        environment="test",
        database_host="127.0.0.1",
        database_port=int(os.environ["AGENTOPSHUB_TEST_PORT"]),
        database_name=name,
        database_user=os.environ["TEST_POSTGRES_USER"],
        database_password=SecretStr(os.environ["TEST_POSTGRES_PASSWORD"]),
    )


@pytest.fixture(scope="session")
def migrated_database(postgres_settings: Settings) -> dict[str, str]:
    env = dict(os.environ)

    def alembic(*args: str) -> str:
        command = [sys.executable, "-m", "alembic", *args]
        if args == ("upgrade", "head"):
            command = [sys.executable, str(ROOT / "scripts/dev.py"), "db-upgrade"]
        elif args == ("current",):
            command = [sys.executable, str(ROOT / "scripts/dev.py"), "db-current"]
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, "Alembic command failed; no database credentials emitted"
        return result.stdout.strip()

    async def tables() -> list[str]:
        db = Database(postgres_settings)
        try:
            async with db.transaction() as session:
                return list(
                    (
                        await session.scalars(
                            text(
                                "SELECT tablename FROM pg_tables "
                                "WHERE schemaname='public' ORDER BY tablename"
                            )
                        )
                    ).all()
                )
        finally:
            await db.close()

    assert asyncio.run(tables()) == []
    alembic("upgrade", "head")
    current = alembic("current")
    assert REVISION in current
    assert asyncio.run(tables()) == [
        "alembic_version",
        "knowledge_chunks",
        "knowledge_document_revisions",
        "knowledge_documents",
        "tenants",
        "tickets",
    ]
    alembic("downgrade", "base")
    assert asyncio.run(tables()) == ["alembic_version"]
    alembic("upgrade", "head")
    reupgraded = alembic("current")
    assert REVISION in reupgraded
    drift = alembic("check")
    evidence = {
        "upgrade": "passed",
        "current": current,
        "downgrade": "passed",
        "reupgrade": reupgraded,
        "metadata_check": drift,
    }
    import json

    artifact = ROOT / ".artifacts"
    artifact.mkdir(exist_ok=True)
    (artifact / "phase1-migrations.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8"
    )
    return evidence


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def database(
    postgres_settings: Settings, migrated_database: dict[str, str]
) -> AsyncIterator[Database]:
    db = Database(postgres_settings)
    try:
        # Dedicated run-owned DB only. Tests are sequential and exercise real commits.
        async with db.transaction() as session:
            await session.execute(delete(KnowledgeChunk))
            await session.execute(delete(DocumentRevision))
            await session.execute(delete(KnowledgeDocument))
            await session.execute(delete(Ticket))
            await session.execute(delete(Tenant))
        yield db
    finally:
        await db.close()
