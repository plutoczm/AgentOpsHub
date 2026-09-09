import logging
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.db.session import Database
from app.observability.logging import JsonFormatter
from app.retrieval.errors import (
    InvalidSearchRequestError,
    RetrievalBackendError,
    RetrievalInvariantError,
)
from app.retrieval.models import (
    KnowledgeRetrievalContext,
    KnowledgeSearchRequest,
    RetrievalResult,
    RetrievedChunk,
)
from app.retrieval.postgres import SEARCH_SQL, PostgresFTSRetriever


def test_trusted_context_and_default_query() -> None:
    tenant = uuid4()
    context = KnowledgeRetrievalContext(
        tenant_id=tenant, namespace="supportops", request_id=uuid4()
    )
    assert context.tenant_id == tenant
    request = KnowledgeSearchRequest(query="  refund  policy\n")
    assert request.query == "refund  policy" and request.top_k == 5
    assert "refund" not in repr(request)


@pytest.mark.parametrize("namespace", ["", "../tenant", "X", "x" * 65])
def test_invalid_context_namespace(namespace: str) -> None:
    with pytest.raises(ValidationError):
        KnowledgeRetrievalContext(tenant_id=uuid4(), namespace=namespace)


@pytest.mark.parametrize(
    "field", ["tenant_id", "namespace", "table", "sql", "rank_expression", "revision_id"]
)
def test_request_forbids_control_plane_injection(field: str) -> None:
    with pytest.raises(ValidationError):
        KnowledgeSearchRequest.model_validate({"query": "x", field: "malicious"})


@pytest.mark.parametrize(
    "query", ["", " \n\t", "x" * 513, " " * 512 + "x", "x\x00", "x\x01", "\ud800"]
)
def test_invalid_query(query: str) -> None:
    with pytest.raises(ValidationError):
        KnowledgeSearchRequest(query=query)


@pytest.mark.parametrize("k", [0, -1, 21, True, "5"])
def test_invalid_top_k(k: object) -> None:
    with pytest.raises(ValidationError):
        KnowledgeSearchRequest.model_validate({"query": "x", "top_k": k})


def test_effective_sql_scope_latest_and_bound_parameters() -> None:
    query = "PRIVATE_QUERY'; DROP TABLE tenants; --"
    statement = SEARCH_SQL.bindparams(
        query=query, tenant_id=uuid4(), namespace="supportops", top_k=3
    )
    compiled = statement.compile()
    sql = str(compiled)
    assert query not in sql and query in compiled.params.values()
    assert "d.tenant_id =" in sql and "d.namespace =" in sql
    assert "newer.document_id = d.id" in sql and "newer.revision_number > r.revision_number" in sql
    assert "NOT EXISTS" in sql and "LIMIT" in sql
    assert 'ORDER BY score DESC, d.source_key COLLATE "C", c.chunk_index, c.id' in sql
    assert "plainto_tsquery" in sql and "pg_catalog.simple" in sql
    assert "ts_rank_cd" in sql and "BM25" not in sql


def row() -> dict[str, object]:
    return {
        "document_id": uuid4(),
        "revision_id": uuid4(),
        "chunk_id": uuid4(),
        "source_key": "PRIVATE_SOURCE.md",
        "title": "PRIVATE_TITLE",
        "namespace": "supportops",
        "chunk_index": 0,
        "section_path": ["PRIVATE_SECTION"],
        "content": "PRIVATE_CHUNK",
        "content_sha256": "a" * 64,
        "character_start": 4,
        "character_end": 17,
        "score": 0.2,
    }


def fake_database(
    monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, object]]
) -> tuple[Database, AsyncMock]:
    db = Database(Settings(_env_file=None))
    session = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    session.execute.return_value = result
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=session)
    manager.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db, "sessions", MagicMock(return_value=manager))
    return db, session


@pytest.mark.anyio
@pytest.mark.parametrize("empty", [False, True])
async def test_result_mapping_query_binding_privacy_and_monotonic_timing(
    empty: bool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    record = row()
    db, session = fake_database(monkeypatch, [] if empty else [record])
    ticks = iter([10.0, 10.25])
    monkeypatch.setattr("app.retrieval.postgres.perf_counter", lambda: next(ticks))
    context = KnowledgeRetrievalContext(
        tenant_id=uuid4(), namespace="supportops", request_id=uuid4()
    )
    with caplog.at_level(logging.INFO):
        result = await PostgresFTSRetriever(db).search(
            context, KnowledgeSearchRequest(query="PRIVATE_QUERY")
        )
    assert result.duration_ms == 250
    assert len(result.chunks) == (0 if empty else 1)
    assert session.execute.call_args.args[1] == {
        "query": "PRIVATE_QUERY",
        "tenant_id": context.tenant_id,
        "namespace": "supportops",
        "top_k": 5,
    }
    session.commit.assert_not_awaited()
    if not empty:
        chunk = result.chunks[0]
        assert chunk.rank == 1 and chunk.score == 0.2
        assert (
            chunk.document_id == record["document_id"]
            and chunk.revision_id == record["revision_id"]
        )
        assert chunk.chunk_id == record["chunk_id"] and chunk.source_key == record["source_key"]
        assert chunk.section_path == ("PRIVATE_SECTION",)
        assert chunk.character_start == 4 and chunk.character_end == 17
        assert "confidence" not in chunk.model_dump()
    raw = repr([r.__dict__ for r in caplog.records])
    rendered = "".join(JsonFormatter().format(r) for r in caplog.records)
    assert "PRIVATE_" not in raw + rendered + repr(result)
    assert str(context.request_id) in rendered


@pytest.mark.anyio
async def test_backend_error_safe_and_not_retried(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db, session = fake_database(monkeypatch, [])
    session.execute.side_effect = RuntimeError("PRIVATE_POSTGRES_ERROR")
    with caplog.at_level(logging.INFO), pytest.raises(RetrievalBackendError) as caught:
        await PostgresFTSRetriever(db).search(
            KnowledgeRetrievalContext(tenant_id=uuid4(), namespace="supportops"),
            KnowledgeSearchRequest(query="PRIVATE_QUERY"),
        )
    assert "PRIVATE_" not in str(caught.value)
    assert caught.value.__suppress_context__
    assert session.execute.await_count == 1
    assert "PRIVATE_" not in repr([r.__dict__ for r in caplog.records])


@pytest.mark.anyio
async def test_unconfigured_database_and_construct_bypass() -> None:
    retriever = PostgresFTSRetriever(Database(Settings(_env_file=None)))
    context = KnowledgeRetrievalContext(tenant_id=uuid4(), namespace="supportops")
    with pytest.raises(RetrievalBackendError):
        await retriever.search(context, KnowledgeSearchRequest(query="x"))
    with pytest.raises(InvalidSearchRequestError):
        await retriever.search(
            context, KnowledgeSearchRequest.model_construct(query=" ", top_k=100)
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invalid", ["range", "score", "namespace", "limit", "duplicate", "missing"]
)
async def test_backend_invariant_validation(invalid: str, monkeypatch: pytest.MonkeyPatch) -> None:
    item = row()
    rows = [item]
    if invalid == "missing":
        item.pop("section_path")
    elif invalid == "range":
        item["character_end"] = 0
    elif invalid == "score":
        item["score"] = float("nan")
    elif invalid == "namespace":
        item["namespace"] = "datacopilot"
    else:
        rows = [item, item] if invalid == "duplicate" else [row() for _ in range(6)]
    db, _ = fake_database(monkeypatch, rows)
    with pytest.raises(RetrievalInvariantError):
        await PostgresFTSRetriever(db).search(
            KnowledgeRetrievalContext(tenant_id=uuid4(), namespace="supportops"),
            KnowledgeSearchRequest(query="x"),
        )


def test_result_rank_contract() -> None:
    chunk = RetrievedChunk.model_validate(
        {**row(), "section_path": ("PRIVATE_SECTION",), "rank": 2}
    )
    with pytest.raises(ValidationError):
        RetrievalResult(retriever="fake", namespace="supportops", chunks=(chunk,), duration_ms=0)


def test_backend_specific_negative_score_is_valid_in_shared_contract() -> None:
    chunk = RetrievedChunk.model_validate({**row(), "section_path": (), "score": -0.2, "rank": 1})
    assert chunk.score == -0.2
