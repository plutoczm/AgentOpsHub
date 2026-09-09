"""Internal deterministic workflow with per-document atomic persistence."""

import logging
from time import perf_counter

from pydantic import ValidationError

from app.db.session import Database
from app.knowledge.chunking import chunk_document, content_hash
from app.knowledge.errors import (
    ChunkingError,
    DocumentValidationError,
    KnowledgeIngestionError,
    KnowledgePersistenceError,
    ParserError,
)
from app.knowledge.models import (
    DocumentInput,
    IngestionConfig,
    IngestionResult,
    IngestionStatus,
    KnowledgeIngestionContext,
)
from app.knowledge.parsing import parse_document
from app.repositories.knowledge import KnowledgeRepository

logger = logging.getLogger(__name__)


class KnowledgeIngestionService:
    """Validate, parse, hash, version and persist; never invoke a model or tool."""

    def __init__(self, database: Database, config: IngestionConfig | None = None) -> None:
        """Use trusted application dependencies without import-time I/O."""
        self.database = database
        self.config = IngestionConfig.model_validate(config or IngestionConfig())

    async def ingest(
        self, source: DocumentInput, context: KnowledgeIngestionContext
    ) -> IngestionResult:
        """Return after commit; external cancellation propagates through transaction rollback."""
        started = perf_counter()
        try:
            source = DocumentInput.model_validate(source)
            context = KnowledgeIngestionContext.model_validate(context)
        except ValidationError:
            raise DocumentValidationError() from None
        metadata: dict[str, object] = {
            "request_id": str(context.request_id) if context.request_id else None,
            "knowledge_namespace": context.namespace,
        }
        logger.info("knowledge_ingest_start", extra=metadata)
        try:
            try:
                parsed = parse_document(source, max_source_bytes=self.config.max_source_bytes)
            except KnowledgeIngestionError:
                raise
            except Exception:
                raise ParserError() from None
            digest = content_hash(parsed.text)
            try:
                async with self.database.transaction() as session:
                    repo = KnowledgeRepository(session, context)
                    document, created = await repo.acquire_source(source)
                    latest = await repo.latest_revision(document.id)
                    if latest is not None and latest.content_sha256 == digest:
                        revision = latest
                        status = IngestionStatus.UNCHANGED
                    else:
                        try:
                            chunks = chunk_document(parsed, self.config.chunking)
                        except Exception:
                            raise ChunkingError() from None
                        revision = await repo.append_revision(
                            source, parsed, self.config.chunking, chunks
                        )
                        status = IngestionStatus.CREATED if created else IngestionStatus.UPDATED
                    result = IngestionResult(
                        status=status,
                        document_id=document.id,
                        revision_id=revision.id,
                        revision_number=revision.revision_number,
                        namespace=context.namespace,
                        content_sha256=revision.content_sha256,
                        chunk_count=revision.chunk_count,
                        duration_ms=(perf_counter() - started) * 1000,
                    )
            except KnowledgeIngestionError:
                raise
            except Exception:
                raise KnowledgePersistenceError() from None
            result = result.model_copy(update={"duration_ms": (perf_counter() - started) * 1000})
            logger.info(
                f"knowledge_ingest_{status.value}",
                extra={
                    **metadata,
                    "knowledge_chunks": result.chunk_count,
                    "knowledge_bytes": len(source.content),
                    "duration_ms": result.duration_ms,
                },
            )
            return result
        except KnowledgeIngestionError as exc:
            logger.warning(
                "knowledge_ingest_error",
                extra={
                    **metadata,
                    "knowledge_error": type(exc).__name__,
                    "duration_ms": (perf_counter() - started) * 1000,
                },
            )
            raise
