"""PostgreSQL FTS baseline: simple lexemes, AND matching and cover-density ranking."""

import logging
from time import perf_counter

from pydantic import ValidationError
from sqlalchemy import text

from app.db.session import Database
from app.retrieval.errors import (
    InvalidSearchRequestError,
    KnowledgeRetrievalError,
    RetrievalBackendError,
    RetrievalInvariantError,
)
from app.retrieval.models import (
    KnowledgeRetrievalContext,
    KnowledgeSearchRequest,
    RetrievalResult,
    RetrievedChunk,
)

logger = logging.getLogger(__name__)
RETRIEVER_NAME = "postgres-fts-simple-cd-v1"
# Only application-owned constants enter SQL text. Every caller value is bound.
SEARCH_SQL = text("""
SELECT d.id AS document_id, r.id AS revision_id, c.id AS chunk_id,
       d.source_key, r.title, d.namespace, c.chunk_index, c.section_path,
       c.content, c.content_sha256, c.character_start, c.character_end,
       ts_rank_cd(to_tsvector('pg_catalog.simple'::regconfig, c.content),
                  plainto_tsquery('pg_catalog.simple'::regconfig, :query), 0) AS score
FROM knowledge_chunks AS c
JOIN knowledge_document_revisions AS r ON r.id = c.revision_id
JOIN knowledge_documents AS d ON d.id = r.document_id
WHERE d.tenant_id = :tenant_id AND d.namespace = :namespace
  AND NOT EXISTS (
      SELECT 1 FROM knowledge_document_revisions AS newer
      WHERE newer.document_id = d.id AND newer.revision_number > r.revision_number
  )
  AND to_tsvector('pg_catalog.simple'::regconfig, c.content)
      @@ plainto_tsquery('pg_catalog.simple'::regconfig, :query)
ORDER BY score DESC, d.source_key COLLATE "C", c.chunk_index, c.id
LIMIT :top_k
""")


class PostgresFTSRetriever:
    """Execute one read-only SELECT; no model, tool, rewrite or Python scope filtering."""

    name = RETRIEVER_NAME

    def __init__(self, database: Database) -> None:
        """Borrow the existing application's bounded database pool."""
        self.database = database

    async def search(
        self,
        context: KnowledgeRetrievalContext,
        request: KnowledgeSearchRequest,
    ) -> RetrievalResult:
        """Return only current-revision chunks, with safe errors and monotonic latency."""
        started = perf_counter()
        try:
            context = KnowledgeRetrievalContext.model_validate(context)
            request = KnowledgeSearchRequest.model_validate(request)
        except ValidationError:
            raise InvalidSearchRequestError() from None
        metadata = {
            "request_id": str(context.request_id) if context.request_id else None,
            "retrieval_namespace": context.namespace,
            "retrieval_top_k": request.top_k,
            "retriever": self.name,
        }
        logger.info("knowledge_search_start", extra=metadata)
        try:
            try:
                if self.database.sessions is None:
                    raise RetrievalBackendError()
                async with self.database.sessions() as session:
                    rows = (
                        (
                            await session.execute(
                                SEARCH_SQL,
                                {
                                    "tenant_id": context.tenant_id,
                                    "namespace": context.namespace,
                                    "query": request.query,
                                    "top_k": request.top_k,
                                },
                            )
                        )
                        .mappings()
                        .all()
                    )
            except Exception:
                raise RetrievalBackendError() from None
            try:
                chunks = tuple(
                    RetrievedChunk(
                        **{**dict(row), "section_path": tuple(row["section_path"]), "rank": rank}
                    )
                    for rank, row in enumerate(rows, 1)
                )
                if len(chunks) > request.top_k:
                    raise ValueError("Result limit violated")
                result = RetrievalResult(
                    retriever=self.name,
                    namespace=context.namespace,
                    chunks=chunks,
                    duration_ms=(perf_counter() - started) * 1000,
                )
            except Exception:
                raise RetrievalInvariantError() from None
            logger.info(
                "knowledge_search_result",
                extra={
                    **metadata,
                    "retrieval_count": len(result.chunks),
                    "duration_ms": result.duration_ms,
                },
            )
            return result
        except KnowledgeRetrievalError as exc:
            logger.warning(
                "knowledge_search_error",
                extra={
                    **metadata,
                    "retrieval_error": type(exc).__name__,
                    "duration_ms": (perf_counter() - started) * 1000,
                },
            )
            raise
