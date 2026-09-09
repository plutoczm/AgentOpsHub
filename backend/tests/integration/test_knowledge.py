import asyncio
import json
import logging
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from app.db.models import DocumentRevision, KnowledgeChunk, KnowledgeDocument
from app.db.session import Database
from app.knowledge.chunking import chunk_document, content_hash
from app.knowledge.errors import ChunkingError, KnowledgePersistenceError
from app.knowledge.models import (
    ChunkDraft,
    ChunkingConfig,
    DocumentInput,
    IngestionResult,
    IngestionStatus,
    KnowledgeIngestionContext,
    ParsedDocument,
)
from app.knowledge.parsing import parse_document
from app.observability.logging import JsonFormatter
from app.repositories.knowledge import KnowledgeRepository
from app.repositories.tenant import TenantRepository
from app.services.knowledge import KnowledgeIngestionService

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def no_agent_ingestion(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Knowledge ingestion invoked an Agent, model or tool")

    monkeypatch.setattr("app.llm.gateway.LLMGateway.generate", forbidden)
    monkeypatch.setattr("app.agents.runtime.AgentRuntime.run", forbidden)
    monkeypatch.setattr("app.tools.executor.ToolExecutor.execute", forbidden)


async def owner(database: Database, slug: str = "owner") -> UUID:
    async with database.transaction() as session:
        return (await TenantRepository(session).create(slug=slug, name=slug)).id


def source(
    body: str = "# Policy\n\nOriginal content.\n", key: str = "policy.md", title: str = "Policy"
) -> DocumentInput:
    return DocumentInput(
        source_key=key, title=title, media_type="text/markdown", content=body.encode()
    )


async def counts(database: Database) -> tuple[int, int, int]:
    async with database.transaction() as session:
        return (
            (await session.scalar(select(func.count()).select_from(KnowledgeDocument))) or 0,
            (await session.scalar(select(func.count()).select_from(DocumentRevision))) or 0,
            (await session.scalar(select(func.count()).select_from(KnowledgeChunk))) or 0,
        )


async def test_revision_lifecycle_idempotency_and_history(database: Database) -> None:
    context = KnowledgeIngestionContext(tenant_id=await owner(database), namespace="supportops")
    service = KnowledgeIngestionService(database)
    first = await service.ingest(source(), context)
    assert first.status is IngestionStatus.CREATED and first.revision_number == 1
    async with database.transaction() as session:
        repo = KnowledgeRepository(session, context)
        before = await repo.get_by_id(first.document_id)
        assert before is not None
        timestamps = before.created_at, before.updated_at
        old_chunks = [
            (c.id, c.content, c.created_at) for c in await repo.list_chunks(first.revision_id)
        ]
    unchanged = await service.ingest(source(title="Ignored title"), context)
    assert (
        unchanged.status is IngestionStatus.UNCHANGED and unchanged.revision_id == first.revision_id
    )
    assert await counts(database) == (1, 1, first.chunk_count)
    async with database.transaction() as session:
        document = await KnowledgeRepository(session, context).get_by_id(first.document_id)
        assert document is not None and (document.created_at, document.updated_at) == timestamps
        assert document.title == "Policy"
    updated = await service.ingest(
        source("# Policy\n\nChanged.\n## New section\nDetails.\n", title="Revised"), context
    )
    assert updated.status is IngestionStatus.UPDATED and updated.revision_number == 2
    assert updated.document_id == first.document_id and updated.revision_id != first.revision_id
    assert await counts(database) == (1, 2, first.chunk_count + updated.chunk_count)
    async with database.transaction() as session:
        repo = KnowledgeRepository(session, context)
        old = await repo.get_revision(first.revision_id)
        assert old is not None and old.normalized_content == source().content.decode()
        assert old.title == "Policy" and old.normalized_bytes == len(source().content)
        assert old.parser_version and old.chunker_version and old.config_sha256
        assert [
            (c.id, c.content, c.created_at) for c in await repo.list_chunks(first.revision_id)
        ] == old_chunks
        current = await repo.get_by_id(first.document_id)
        assert current is not None and current.title == "Revised"
        assert current.updated_at > timestamps[1]
        latest = await repo.latest_revision(first.document_id)
        assert latest is not None and latest.id == updated.revision_id
    # Reverting content creates a new historical event rather than deleting version 2.
    reverted = await service.ingest(source(), context)
    assert reverted.revision_number == 3 and reverted.content_sha256 == first.content_sha256


@pytest.mark.parametrize("scope_change", ["tenant", "namespace"])
async def test_scoped_reads_warm_identity_map_and_same_source(
    database: Database,
    scope_change: str,
) -> None:
    tenant_a, tenant_b = await owner(database, "a"), await owner(database, "b")
    a = KnowledgeIngestionContext(tenant_id=tenant_a, namespace="supportops")
    b = KnowledgeIngestionContext(
        tenant_id=tenant_b if scope_change == "tenant" else tenant_a,
        namespace="supportops" if scope_change == "tenant" else "datacopilot",
    )
    service = KnowledgeIngestionService(database)
    own = await service.ingest(source(), a)
    other = await service.ingest(source("# Private\nTenant B data"), b)
    assert own.document_id != other.document_id
    async with database.transaction() as session:
        scoped = KnowledgeRepository(session, a)
        target = KnowledgeRepository(session, b)
        assert await target.get_by_id(other.document_id) is not None
        assert await target.get_revision(other.revision_id) is not None
        assert await target.list_chunks(other.revision_id)
        assert await scoped.get_by_id(other.document_id) is None
        assert await scoped.get_revision(other.revision_id) is None
        assert await scoped.latest_revision(other.document_id) is None
        assert await scoped.list_chunks(other.revision_id) == []
        assert [d.id for d in await scoped.list_documents()] == [own.document_id]
        found = await scoped.get_by_source("policy.md")
        assert found is not None and found.id == own.document_id


async def test_source_identity_is_not_title_and_pagination(database: Database) -> None:
    context = KnowledgeIngestionContext(tenant_id=await owner(database), namespace="supportops")
    service = KnowledgeIngestionService(database)
    a = await service.ingest(source(key="a.md"), context)
    b = await service.ingest(source(key="b.md"), context)
    assert a.document_id != b.document_id and a.content_sha256 == b.content_sha256
    async with database.transaction() as session:
        repo = KnowledgeRepository(session, context)
        assert [d.id for d in await repo.list_documents(limit=1, offset=1)] == [b.document_id]
        assert await repo.get_by_source("missing") is None
        for limit, offset in [(0, 0), (101, 0), (1, -1)]:
            with pytest.raises(ValueError):
                await repo.list_documents(limit=limit, offset=offset)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["chunk", "database"])
async def test_real_atomic_rollback(
    database: Database,
    existing: bool,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    context = KnowledgeIngestionContext(tenant_id=await owner(database), namespace="supportops")
    service = KnowledgeIngestionService(database)
    if existing:
        await service.ingest(source(), context)
    before = await counts(database)
    if failure == "chunk":

        def broken(*args: object, **kwargs: object) -> None:
            raise RuntimeError("PRIVATE_PARSER_ERROR")

        monkeypatch.setattr("app.services.knowledge.chunk_document", broken)
        expected: type[Exception] = ChunkingError
    else:
        original = KnowledgeRepository.append_revision

        async def broken_write(
            self: KnowledgeRepository,
            source: DocumentInput,
            parsed: ParsedDocument,
            config: ChunkingConfig,
            chunks: tuple[ChunkDraft, ...],
        ) -> DocumentRevision:
            # Fail after actual document/revision/chunks have all been flushed.
            result = await original(self, source, parsed, config, chunks)
            await self.session.execute(
                insert(KnowledgeChunk).values(
                    id=uuid4(),
                    revision_id=result.id,
                    chunk_index=0,
                    content="PRIVATE_DB_ERROR",
                    content_sha256=content_hash("PRIVATE_DB_ERROR"),
                    section_path=[],
                    character_start=0,
                    character_end=16,
                )
            )
            return result

        monkeypatch.setattr(KnowledgeRepository, "append_revision", broken_write)
        expected = KnowledgePersistenceError
    with pytest.raises(expected) as caught:
        await service.ingest(source("# Changed\nNever partially committed"), context)
    assert "PRIVATE_" not in str(caught.value)
    assert await counts(database) == before
    raw = repr([record.__dict__ for record in caplog.records])
    rendered = "".join(JsonFormatter().format(record) for record in caplog.records)
    assert "PRIVATE_" not in raw + rendered
    assert "Never partially committed" not in raw + rendered


@pytest.mark.parametrize("constraint", ["source", "revision", "chunk"])
async def test_database_uniqueness(database: Database, constraint: str) -> None:
    context = KnowledgeIngestionContext(tenant_id=await owner(database), namespace="supportops")
    result = await KnowledgeIngestionService(database).ingest(source(), context)
    with pytest.raises(IntegrityError):
        async with database.transaction() as session:
            if constraint == "source":
                session.add(
                    KnowledgeDocument(
                        tenant_id=context.tenant_id,
                        namespace=context.namespace,
                        source_key="policy.md",
                        title="duplicate",
                        media_type="text/markdown",
                    )
                )
            elif constraint == "chunk":
                session.add(
                    KnowledgeChunk(
                        revision_id=result.revision_id,
                        chunk_index=0,
                        content="x",
                        content_sha256=content_hash("x"),
                        section_path=[],
                        character_start=0,
                        character_end=1,
                    )
                )
            else:
                current = await KnowledgeRepository(session, context).get_revision(
                    result.revision_id
                )
                assert current is not None
                values = {
                    column.name: getattr(current, column.name)
                    for column in DocumentRevision.__table__.columns
                    if column.name != "id"
                }
                session.add(DocumentRevision(id=uuid4(), **values))
            await session.flush()
    assert await counts(database) == (1, 1, result.chunk_count)


@pytest.mark.parametrize("existing", [False, True])
async def test_concurrent_duplicate_ingestion(database: Database, existing: bool) -> None:
    context = KnowledgeIngestionContext(tenant_id=await owner(database), namespace="supportops")
    service = KnowledgeIngestionService(database)
    if existing:
        await service.ingest(source(), context)
    gate = asyncio.Event()

    async def ingest() -> IngestionResult:
        await gate.wait()
        return await service.ingest(source("# New\nConcurrent content"), context)

    tasks = [asyncio.create_task(ingest()) for _ in range(5)]
    gate.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)
    statuses = [r.status for r in results]
    assert statuses.count(IngestionStatus.UNCHANGED) == 4
    assert statuses.count(IngestionStatus.UPDATED if existing else IngestionStatus.CREATED) == 1
    assert await counts(database) == (1, 2 if existing else 1, 2 if existing else 1)


async def test_both_reference_corpora_generic_pipeline_and_isolation(database: Database) -> None:
    tenant_a, tenant_b = await owner(database, "a"), await owner(database, "b")
    service = KnowledgeIngestionService(database)
    evidence: list[dict[str, object]] = []
    for path in sorted((ROOT / "examples/knowledge").glob("*/*.md")):
        context = KnowledgeIngestionContext(tenant_id=tenant_a, namespace=path.parent.name)
        document = DocumentInput(
            source_key=path.name,
            title=path.stem,
            media_type="text/markdown",
            content=path.read_bytes(),
        )
        first = await service.ingest(document, context)
        repeat = await service.ingest(document, context)
        assert (
            first.status is IngestionStatus.CREATED and repeat.status is IngestionStatus.UNCHANGED
        )
        assert first.revision_id == repeat.revision_id
        parsed = parse_document(document, max_source_bytes=1048576)
        expected = chunk_document(parsed, ChunkingConfig())
        async with database.transaction() as session:
            repo = KnowledgeRepository(session, context)
            chunks = await repo.list_chunks(first.revision_id)
            assert len(chunks) == len(expected) == 3
            for actual, draft in zip(chunks, expected, strict=True):
                assert actual.content == draft.content
                assert actual.content_sha256 == draft.content_sha256
                assert actual.character_start == draft.character_start
                assert actual.character_end == draft.character_end
                assert tuple(actual.section_path) == draft.section_path
                assert actual.revision_id == first.revision_id
                assert actual.created_at.utcoffset() is not None
            for foreign in [
                KnowledgeIngestionContext(tenant_id=tenant_b, namespace=context.namespace),
                KnowledgeIngestionContext(
                    tenant_id=tenant_a,
                    namespace="datacopilot" if context.namespace == "supportops" else "supportops",
                ),
            ]:
                foreign_repo = KnowledgeRepository(session, foreign)
                assert await foreign_repo.get_by_id(first.document_id) is None
                assert await foreign_repo.list_chunks(first.revision_id) == []
        evidence.append(
            {
                "namespace": context.namespace,
                "source_key": path.name,
                "chunks": first.chunk_count,
                "sha256": first.content_sha256,
                "unchanged": True,
                "provenance_complete": True,
            }
        )
    assert await counts(database) == (6, 6, 18)
    (ROOT / ".artifacts/phase5-ingestion.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8"
    )
