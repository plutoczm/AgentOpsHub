import json
import logging
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, inspect, select, text

from app.db.models import DocumentRevision, KnowledgeChunk, KnowledgeDocument
from app.db.session import Database
from app.evaluation.benchmark import run_benchmark
from app.evaluation.retrieval import deterministic_signature
from app.knowledge.models import DocumentInput, KnowledgeIngestionContext
from app.observability.logging import JsonFormatter
from app.repositories.knowledge import KnowledgeRepository
from app.repositories.tenant import TenantRepository
from app.retrieval.errors import RetrievalBackendError
from app.retrieval.models import KnowledgeRetrievalContext, KnowledgeSearchRequest
from app.retrieval.postgres import SEARCH_SQL, PostgresFTSRetriever
from app.services.knowledge import KnowledgeIngestionService

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def no_agent_or_external_api(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Retrieval invoked Agent/LLM/tool/external API")

    monkeypatch.setattr("app.llm.gateway.LLMGateway.generate", forbidden)
    monkeypatch.setattr("app.agents.runtime.AgentRuntime.run", forbidden)
    monkeypatch.setattr("app.tools.executor.ToolExecutor.execute", forbidden)
    monkeypatch.setattr("httpx2.AsyncClient.send", forbidden)


async def tenant(database: Database, slug: str = "search") -> UUID:
    async with database.transaction() as session:
        return (await TenantRepository(session).create(slug=slug, name=slug)).id


async def ingest(database: Database, owner: UUID, namespace: str, key: str, body: str) -> UUID:
    result = await KnowledgeIngestionService(database).ingest(
        DocumentInput(
            source_key=key, title="Synthetic", media_type="text/markdown", content=body.encode()
        ),
        KnowledgeIngestionContext(tenant_id=owner, namespace=namespace),
    )
    return result.revision_id


async def test_fts_index_presence_and_usable_expression(database: Database) -> None:
    owner = await tenant(database)
    await ingest(database, owner, "supportops", "a.md", "amber")
    assert database.engine is not None
    async with database.engine.connect() as connection:
        indexes = await connection.run_sync(lambda c: inspect(c).get_indexes("knowledge_chunks"))
    index = next(i for i in indexes if i["name"] == "ix_knowledge_chunks_fts")
    assert index["dialect_options"]["postgresql_using"] == "gin"
    assert "to_tsvector('simple'::regconfig, content)" in index["expressions"]
    async with database.transaction() as session:
        # Diagnostic only: prove index eligibility, not that a tiny corpus needs an index scan.
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = await session.scalar(
            text(
                "EXPLAIN (FORMAT JSON) SELECT id FROM knowledge_chunks "
                "WHERE to_tsvector('pg_catalog.simple'::regconfig, content) "
                "@@ plainto_tsquery('pg_catalog.simple'::regconfig, :query)"
            ),
            {
                "tenant_id": owner,
                "namespace": "supportops",
                "query": "amber",
                "top_k": 5,
            },
        )
        assert "ix_knowledge_chunks_fts" in json.dumps(plan)


@pytest.mark.parametrize(
    "namespace,query", [("supportops", "unopened 14"), ("datacopilot", "SQL AST")]
)
async def test_reference_fixture_exact_terms_and_provenance(
    database: Database,
    namespace: str,
    query: str,
) -> None:
    owner = await tenant(database)
    key = "refund_policy.md" if namespace == "supportops" else "sql_standards.md"
    path = ROOT / "examples/knowledge" / namespace / key
    revision = await ingest(database, owner, namespace, key, path.read_text(encoding="utf-8"))
    result = await PostgresFTSRetriever(database).search(
        KnowledgeRetrievalContext(tenant_id=owner, namespace=namespace),
        KnowledgeSearchRequest(query=query),
    )
    assert result.chunks
    async with database.transaction() as session:
        repo = KnowledgeRepository(
            session, KnowledgeIngestionContext(tenant_id=owner, namespace=namespace)
        )
        stored = {c.id: c for c in await repo.list_chunks(revision)}
        version = await repo.get_revision(revision)
        assert version is not None
        for rank, chunk in enumerate(result.chunks, 1):
            assert chunk.revision_id == revision and chunk.document_id == version.document_id
            assert chunk.chunk_id in stored and chunk.source_key == key and chunk.rank == rank
            assert chunk.content == stored[chunk.chunk_id].content
            assert chunk.section_path == tuple(stored[chunk.chunk_id].section_path)
            assert (
                version.normalized_content[chunk.character_start : chunk.character_end]
                == chunk.content
            )


async def test_latest_revision_only_and_unchanged_identity(database: Database) -> None:
    owner = await tenant(database)
    old = await ingest(database, owner, "supportops", "policy.md", "retiredtoken refund 30 days")
    retriever = PostgresFTSRetriever(database)
    context = KnowledgeRetrievalContext(tenant_id=owner, namespace="supportops")
    assert (await retriever.search(context, KnowledgeSearchRequest(query="retiredtoken"))).chunks
    current = await ingest(
        database, owner, "supportops", "policy.md", "currenttoken refund 14 days"
    )
    assert current != old
    assert not (
        await retriever.search(context, KnowledgeSearchRequest(query="retiredtoken"))
    ).chunks
    found = await retriever.search(context, KnowledgeSearchRequest(query="currenttoken"))
    assert found.chunks and all(c.revision_id == current for c in found.chunks)
    same = await ingest(database, owner, "supportops", "policy.md", "currenttoken refund 14 days")
    repeated = await retriever.search(context, KnowledgeSearchRequest(query="currenttoken"))
    assert same == current and repeated.chunks == found.chunks
    async with database.transaction() as session:
        history = await KnowledgeRepository(
            session,
            KnowledgeIngestionContext(
                tenant_id=owner,
                namespace="supportops",
            ),
        ).get_revision(old)
        assert history is not None and "retiredtoken" in history.normalized_content


@pytest.mark.parametrize("foreign_namespace", ["supportops", "datacopilot"])
async def test_cross_tenant_search_exact_foreign_term_and_unchanged_records(
    database: Database,
    foreign_namespace: str,
) -> None:
    a, b = await tenant(database, "a"), await tenant(database, "b")
    await ingest(database, a, "supportops", "shared.md", "owncontent")
    foreign = await ingest(database, b, foreign_namespace, "shared.md", "FOREIGN_PRIVATE_MARKER")
    # Warm the ORM identity map on the same read session used by retrieval.
    assert database.sessions is not None
    async with database.sessions() as session:
        stored = await KnowledgeRepository(
            session,
            KnowledgeIngestionContext(
                tenant_id=b,
                namespace=foreign_namespace,
            ),
        ).get_revision(foreign)
        assert stored is not None
        parameters = {
            "tenant_id": a,
            "namespace": "supportops",
            "query": "FOREIGN_PRIVATE_MARKER",
            "top_k": 5,
        }
        assert not (await session.execute(SEARCH_SQL, parameters)).all()
    result = await PostgresFTSRetriever(database).search(
        KnowledgeRetrievalContext(tenant_id=a, namespace="supportops"),
        KnowledgeSearchRequest(query="FOREIGN_PRIVATE_MARKER"),
    )
    assert not result.chunks
    async with database.transaction() as session:
        stored = await KnowledgeRepository(
            session,
            KnowledgeIngestionContext(
                tenant_id=b,
                namespace=foreign_namespace,
            ),
        ).get_revision(foreign)
        assert stored is not None and stored.normalized_content == "FOREIGN_PRIVATE_MARKER"


@pytest.mark.parametrize("namespace", ["supportops", "datacopilot"])
async def test_same_keyword_cross_namespace_isolation(database: Database, namespace: str) -> None:
    owner = await tenant(database)
    revisions = {
        ns: await ingest(database, owner, ns, "shared.md", "sharedkeyword")
        for ns in ("supportops", "datacopilot")
    }
    result = await PostgresFTSRetriever(database).search(
        KnowledgeRetrievalContext(tenant_id=owner, namespace=namespace),
        KnowledgeSearchRequest(query="sharedkeyword"),
    )
    assert len(result.chunks) == 1
    assert result.chunks[0].namespace == namespace
    assert result.chunks[0].revision_id == revisions[namespace]


@pytest.mark.parametrize("top_k", [1, 3, 5])
async def test_top_k_score_and_stable_tie_breaking(database: Database, top_k: int) -> None:
    owner = await tenant(database)
    for key in ["z.md", "c.md", "b.md", "a.md", "A.md", "d.md"]:
        await ingest(database, owner, "supportops", key, "amber")
    await ingest(database, owner, "supportops", "repeated.md", "amber amber amber")
    context = KnowledgeRetrievalContext(tenant_id=owner, namespace="supportops")
    retriever = PostgresFTSRetriever(database)
    first = await retriever.search(context, KnowledgeSearchRequest(query="amber", top_k=top_k))
    assert len(first.chunks) == top_k
    assert [c.source_key for c in first.chunks] == ["repeated.md", "A.md", "a.md", "b.md", "c.md"][
        :top_k
    ]
    assert [c.rank for c in first.chunks] == list(range(1, top_k + 1))
    assert [c.score for c in first.chunks] == sorted((c.score for c in first.chunks), reverse=True)
    for _ in range(3):
        assert (
            await retriever.search(context, KnowledgeSearchRequest(query="amber", top_k=top_k))
        ).chunks == first.chunks


async def test_chunk_index_ties_preserve_order(database: Database) -> None:
    owner = await tenant(database)
    await ingest(database, owner, "supportops", "same.md", "## First\namber\n## Second\namber\n")
    result = await PostgresFTSRetriever(database).search(
        KnowledgeRetrievalContext(tenant_id=owner, namespace="supportops"),
        KnowledgeSearchRequest(query="amber"),
    )
    assert [c.chunk_index for c in result.chunks] == [0, 1]


@pytest.mark.parametrize(
    "query", ["nevermatches", "!!!", "amber'; DROP TABLE tenants; --", "amber' OR 1=1 --"]
)
async def test_no_match_and_sql_special_input_is_safe(database: Database, query: str) -> None:
    owner = await tenant(database)
    await ingest(database, owner, "supportops", "a.md", "amber")
    result = await PostgresFTSRetriever(database).search(
        KnowledgeRetrievalContext(tenant_id=owner, namespace="supportops"),
        KnowledgeSearchRequest(query=query),
    )
    assert result.chunks == ()
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(KnowledgeDocument)) == 1
        assert await session.scalar(select(func.count()).select_from(DocumentRevision)) == 1


async def test_multi_term_and_configuration_limits(database: Database) -> None:
    owner = await tenant(database)
    await ingest(database, owner, "supportops", "a.md", "amber blue")
    await ingest(database, owner, "supportops", "b.md", "amber")
    retriever = PostgresFTSRetriever(database)
    context = KnowledgeRetrievalContext(tenant_id=owner, namespace="supportops")
    both = await retriever.search(context, KnowledgeSearchRequest(query="amber & blue"))
    assert [c.source_key for c in both.chunks] == ["a.md"]
    # simple does not stem plural forms or add synonyms.
    assert not (await retriever.search(context, KnowledgeSearchRequest(query="ambers"))).chunks


async def test_real_query_chunk_and_error_logging_privacy(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = await tenant(database)
    await ingest(database, owner, "supportops", "a.md", "PRIVATE_QUERY PRIVATE_CHUNK")
    context = KnowledgeRetrievalContext(tenant_id=owner, namespace="supportops", request_id=uuid4())
    retriever = PostgresFTSRetriever(database)
    with caplog.at_level(logging.INFO):
        assert (
            await retriever.search(context, KnowledgeSearchRequest(query="PRIVATE_QUERY"))
        ).chunks
        monkeypatch.setattr(
            "app.retrieval.postgres.SEARCH_SQL", text("SELECT 1 FROM PRIVATE_MISSING_TABLE")
        )
        with pytest.raises(RetrievalBackendError) as caught:
            await retriever.search(context, KnowledgeSearchRequest(query="PRIVATE_QUERY"))
    assert "PRIVATE_" not in str(caught.value)
    raw = repr([r.__dict__ for r in caplog.records])
    rendered = "".join(JsonFormatter().format(r) for r in caplog.records)
    assert "PRIVATE_" not in raw + rendered
    assert str(context.request_id) in rendered


async def test_complete_synthetic_benchmark_reproducibility(database: Database) -> None:
    evidence = await run_benchmark(database, ROOT)
    assert evidence.postgres_version.startswith("17.")
    assert [(d.documents, d.queries) for d in evidence.datasets] == [(8, 24), (8, 24)]
    assert evidence.repeat_identical and evidence.unchanged_documents == 16
    assert [r.query_count for r in evidence.reports] == [24, 24, 48]
    for report, repeat in zip(evidence.reports, evidence.repeat_reports, strict=True):
        assert deterministic_signature(report) == deterministic_signature(repeat)
        assert len(report.groups) == 8
        assert [m.k for m in report.groups[0].metrics] == [1, 3, 5]
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(KnowledgeDocument)) == 16
        assert await session.scalar(select(func.count()).select_from(DocumentRevision)) == 16
        assert (await session.scalar(select(func.count()).select_from(KnowledgeChunk))) == 48
    (ROOT / ".artifacts/phase6-integration-benchmark.json").write_text(
        evidence.model_dump_json(indent=2),
        encoding="utf-8",
    )


async def test_terms_split_across_sections_do_not_match_a_single_chunk(database: Database) -> None:
    owner = await tenant(database)
    await ingest(
        database,
        owner,
        "supportops",
        "policy.md",
        "# Refund\nIntroduction.\n## Eligibility\nUnopened item.",
    )
    result = await PostgresFTSRetriever(database).search(
        KnowledgeRetrievalContext(tenant_id=owner, namespace="supportops"),
        KnowledgeSearchRequest(query="refund eligibility"),
    )
    assert result.chunks == ()


async def test_simple_configuration_does_not_segment_chinese_subwords(database: Database) -> None:
    owner = await tenant(database)
    await ingest(
        database, owner, "supportops", "chinese.md", "\u9000\u6b3e\u653f\u7b56\u89c4\u5b9a"
    )
    retriever = PostgresFTSRetriever(database)
    context = KnowledgeRetrievalContext(tenant_id=owner, namespace="supportops")
    assert (
        await retriever.search(
            context, KnowledgeSearchRequest(query="\u9000\u6b3e\u653f\u7b56\u89c4\u5b9a")
        )
    ).chunks
    assert not (
        await retriever.search(context, KnowledgeSearchRequest(query="\u9000\u6b3e"))
    ).chunks
