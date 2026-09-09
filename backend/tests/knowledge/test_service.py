import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import DocumentRevision, KnowledgeDocument
from app.db.session import Database
from app.knowledge.chunking import content_hash
from app.knowledge.errors import (
    ChunkingError,
    DocumentValidationError,
    KnowledgePersistenceError,
    ParserError,
)
from app.knowledge.models import DocumentInput, IngestionStatus, KnowledgeIngestionContext
from app.observability.logging import JsonFormatter
from app.services.knowledge import KnowledgeIngestionService


class FakeDatabase(Database):
    def __init__(self) -> None:
        super().__init__(Settings(_env_file=None))
        self.committed = False
        self.rolled_back = False
        self.session = AsyncMock(spec=AsyncSession)
        self.fail_commit = False

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        try:
            yield self.session
            if self.fail_commit:
                raise RuntimeError("PRIVATE_DB_BODY")
            self.committed = True
        except BaseException:
            self.rolled_back = True
            raise


@pytest.mark.anyio
@pytest.mark.parametrize("status", list(IngestionStatus))
async def test_service_status_privacy_and_monotonic_duration(
    status: IngestionStatus,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = FakeDatabase()
    context = KnowledgeIngestionContext(
        tenant_id=uuid4(), namespace="supportops", request_id=uuid4()
    )
    body = "PRIVATE_DOCUMENT_MARKER\nPRIVATE_CHUNK_MARKER"
    source = DocumentInput(
        source_key="private-key",
        title="PRIVATE_TITLE",
        media_type="text/plain",
        content=body.encode(),
    )
    document = KnowledgeDocument(
        id=uuid4(), tenant_id=context.tenant_id, namespace=context.namespace
    )
    revision = DocumentRevision(
        id=uuid4(),
        document_id=document.id,
        revision_number=1,
        content_sha256=content_hash(body),
        chunk_count=1,
    )
    repo = MagicMock()
    repo.acquire_source = AsyncMock(return_value=(document, status is IngestionStatus.CREATED))
    previous = None if status is IngestionStatus.CREATED else revision
    if status is IngestionStatus.UPDATED:
        previous = DocumentRevision(id=uuid4(), content_sha256=content_hash("old"))
    repo.latest_revision = AsyncMock(return_value=previous)
    repo.append_revision = AsyncMock(return_value=revision)
    factory = MagicMock(return_value=repo)
    monkeypatch.setattr("app.services.knowledge.KnowledgeRepository", factory)
    ticks = iter([10.0, 10.25, 10.5])
    monkeypatch.setattr("app.services.knowledge.perf_counter", lambda: next(ticks))
    if status is IngestionStatus.UNCHANGED:
        monkeypatch.setattr(
            "app.services.knowledge.chunk_document",
            MagicMock(side_effect=AssertionError("must not chunk")),
        )
    with caplog.at_level(logging.INFO):
        result = await KnowledgeIngestionService(db).ingest(source, context)
    assert result.status is status and result.duration_ms == 500
    assert db.committed and not db.rolled_back
    assert factory.call_args.args == (db.session, context)
    assert result.namespace == context.namespace
    if status is IngestionStatus.UNCHANGED:
        repo.append_revision.assert_not_awaited()
    else:
        repo.append_revision.assert_awaited_once()
    raw = repr([r.__dict__ for r in caplog.records])
    formatted = "\n".join(JsonFormatter().format(r) for r in caplog.records)
    for marker in [
        body,
        "PRIVATE_DOCUMENT_MARKER",
        "PRIVATE_CHUNK_MARKER",
        "PRIVATE_TITLE",
        "private-key",
    ]:
        assert marker not in raw and marker not in formatted
    assert str(context.request_id) in formatted


@pytest.mark.anyio
@pytest.mark.parametrize(
    "stage,error",
    [
        ("parse", ParserError),
        ("chunk", ChunkingError),
        ("write", KnowledgePersistenceError),
        ("commit", KnowledgePersistenceError),
    ],
)
async def test_failure_mapping_and_rollback(
    stage: str,
    error: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = FakeDatabase()
    context = KnowledgeIngestionContext(tenant_id=uuid4(), namespace="datacopilot")
    source = DocumentInput(
        source_key="x", title="x", media_type="text/plain", content=b"PRIVATE_DOCUMENT_MARKER"
    )
    repo = MagicMock()
    repo.acquire_source = AsyncMock(return_value=(KnowledgeDocument(id=uuid4()), True))
    repo.latest_revision = AsyncMock(return_value=None)
    revision = DocumentRevision(
        id=uuid4(), revision_number=1, content_sha256=content_hash("x"), chunk_count=1
    )
    repo.append_revision = AsyncMock(return_value=revision)
    monkeypatch.setattr("app.services.knowledge.KnowledgeRepository", MagicMock(return_value=repo))
    if stage in ("parse", "chunk"):
        target = "parse_document" if stage == "parse" else "chunk_document"
        monkeypatch.setattr(
            f"app.services.knowledge.{target}",
            MagicMock(side_effect=RuntimeError("PRIVATE_DB_BODY")),
        )
    elif stage == "write":
        repo.append_revision.side_effect = RuntimeError("PRIVATE_DB_BODY")
    else:
        db.fail_commit = True
    with caplog.at_level(logging.INFO), pytest.raises(error) as caught:
        await KnowledgeIngestionService(db).ingest(source, context)
    assert "PRIVATE_DB_BODY" not in str(caught.value)
    assert caught.value.__suppress_context__
    assert not db.committed
    assert db.rolled_back is (stage != "parse")
    raw = repr([r.__dict__ for r in caplog.records])
    formatted = "\n".join(JsonFormatter().format(r) for r in caplog.records)
    assert "PRIVATE_DB_BODY" not in raw + formatted
    assert "PRIVATE_DOCUMENT_MARKER" not in raw + formatted


@pytest.mark.anyio
async def test_revalidation_rejects_construct_bypass() -> None:
    source = DocumentInput.model_construct(
        source_key="../x", title="x", media_type="text/plain", content=b"x"
    )
    with pytest.raises(DocumentValidationError):
        await KnowledgeIngestionService(FakeDatabase()).ingest(
            source,
            KnowledgeIngestionContext(tenant_id=uuid4(), namespace="supportops"),
        )
