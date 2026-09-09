"""Focused scoped SQL APIs; services own commit and rollback."""

from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge import DocumentRevision, KnowledgeChunk, KnowledgeDocument
from app.knowledge.chunking import CHUNKER_VERSION, content_hash
from app.knowledge.models import (
    ChunkDraft,
    ChunkingConfig,
    DocumentInput,
    KnowledgeIngestionContext,
    ParsedDocument,
)


class KnowledgeRepository:
    """Bind trusted ownership once; descendant reads join the scoped document."""

    def __init__(self, session: AsyncSession, context: KnowledgeIngestionContext) -> None:
        """Borrow a caller-owned transaction without sharing sessions across tasks."""
        self.session = session
        self.context = KnowledgeIngestionContext.model_validate(context)

    async def get_by_id(self, document_id: UUID) -> KnowledgeDocument | None:
        """Apply SQL predicates even with a warm identity map."""
        return (
            await self.session.scalars(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.tenant_id == self.context.tenant_id,
                    KnowledgeDocument.namespace == self.context.namespace,
                    KnowledgeDocument.id == document_id,
                )
            )
        ).one_or_none()

    async def get_by_source(self, source_key: str) -> KnowledgeDocument | None:
        """Resolve source identity within this collection."""
        return (
            await self.session.scalars(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.tenant_id == self.context.tenant_id,
                    KnowledgeDocument.namespace == self.context.namespace,
                    KnowledgeDocument.source_key == source_key,
                )
            )
        ).one_or_none()

    async def list_documents(
        self, *, limit: int = 50, offset: int = 0
    ) -> Sequence[KnowledgeDocument]:
        """List a bounded page in stable source order."""
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid pagination")
        return (
            await self.session.scalars(
                select(KnowledgeDocument)
                .where(
                    KnowledgeDocument.tenant_id == self.context.tenant_id,
                    KnowledgeDocument.namespace == self.context.namespace,
                )
                .order_by(KnowledgeDocument.source_key, KnowledgeDocument.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()

    async def acquire_source(self, source: DocumentInput) -> tuple[KnowledgeDocument, bool]:
        """Insert if absent and hold a scoped row lock until transaction completion."""
        created_id = await self.session.scalar(
            insert(KnowledgeDocument)
            .values(
                id=uuid4(),
                tenant_id=self.context.tenant_id,
                namespace=self.context.namespace,
                source_key=source.source_key,
                title=source.title,
                media_type=source.media_type,
            )
            .on_conflict_do_nothing(constraint="uq_knowledge_source")
            .returning(KnowledgeDocument.id)
        )
        document = (
            await self.session.scalars(
                select(KnowledgeDocument)
                .where(
                    KnowledgeDocument.tenant_id == self.context.tenant_id,
                    KnowledgeDocument.namespace == self.context.namespace,
                    KnowledgeDocument.source_key == source.source_key,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one()
        return document, created_id is not None

    async def latest_revision(self, document_id: UUID) -> DocumentRevision | None:
        """Read the latest version with parent ownership constraints."""
        return (
            await self.session.scalars(
                select(DocumentRevision)
                .join(KnowledgeDocument)
                .where(
                    KnowledgeDocument.tenant_id == self.context.tenant_id,
                    KnowledgeDocument.namespace == self.context.namespace,
                    KnowledgeDocument.id == document_id,
                )
                .order_by(DocumentRevision.revision_number.desc())
                .limit(1)
            )
        ).one_or_none()

    async def get_revision(self, revision_id: UUID) -> DocumentRevision | None:
        """Resolve a historical revision through its owned parent."""
        return (
            await self.session.scalars(
                select(DocumentRevision)
                .join(KnowledgeDocument)
                .where(
                    KnowledgeDocument.tenant_id == self.context.tenant_id,
                    KnowledgeDocument.namespace == self.context.namespace,
                    DocumentRevision.id == revision_id,
                )
            )
        ).one_or_none()

    async def list_chunks(self, revision_id: UUID) -> Sequence[KnowledgeChunk]:
        """Read an ordered, bounded revision through both parent joins."""
        return (
            await self.session.scalars(
                select(KnowledgeChunk)
                .join(DocumentRevision)
                .join(KnowledgeDocument)
                .where(
                    KnowledgeDocument.tenant_id == self.context.tenant_id,
                    KnowledgeDocument.namespace == self.context.namespace,
                    DocumentRevision.id == revision_id,
                )
                .order_by(KnowledgeChunk.chunk_index)
                .limit(8192)
            )
        ).all()

    async def append_revision(
        self,
        source: DocumentInput,
        parsed: ParsedDocument,
        config: ChunkingConfig,
        chunks: tuple[ChunkDraft, ...],
    ) -> DocumentRevision:
        """Recheck locked identity and append atomically; never overwrite old revisions."""
        document, _ = await self.acquire_source(source)
        latest = await self.latest_revision(document.id)
        if latest is not None and latest.content_sha256 == content_hash(parsed.text):
            return latest
        revision = DocumentRevision(
            document_id=document.id,
            revision_number=1 if latest is None else latest.revision_number + 1,
            content_sha256=content_hash(parsed.text),
            normalized_content=parsed.text,
            normalized_bytes=len(parsed.text.encode("utf-8")),
            normalized_chars=len(parsed.text),
            title=source.title,
            media_type=source.media_type,
            parser_version=parsed.parser_version,
            chunker_version=CHUNKER_VERSION,
            config_sha256=config.fingerprint,
            max_chars=config.max_chars,
            overlap_chars=config.overlap_chars,
            chunk_count=len(chunks),
        )
        self.session.add(revision)
        await self.session.flush()
        self.session.add_all(
            [
                KnowledgeChunk(
                    revision_id=revision.id,
                    **chunk.model_dump(exclude={"section_path"}),
                    section_path=list(chunk.section_path),
                )
                for chunk in chunks
            ]
        )
        if latest is not None:
            await self.session.execute(
                update(KnowledgeDocument)
                .where(
                    KnowledgeDocument.id == document.id,
                    KnowledgeDocument.tenant_id == self.context.tenant_id,
                    KnowledgeDocument.namespace == self.context.namespace,
                )
                .values(title=source.title, media_type=source.media_type)
            )
        await self.session.flush()
        return revision
