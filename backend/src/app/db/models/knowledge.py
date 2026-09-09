"""Normalized tenant-owned sources, revision snapshots and chunk provenance."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps


class KnowledgeDocument(Timestamps, Base):
    """Logical identity within one tenant and collection."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "namespace", "source_key", name="uq_knowledge_source"),
        CheckConstraint("namespace ~ '^[a-z][a-z0-9_-]{0,63}$'", name="namespace_valid"),
        CheckConstraint(
            "source_key ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$' "
            "AND position('..' in source_key) = 0",
            name="source_key_valid",
        ),
        CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
        CheckConstraint("media_type IN ('text/plain', 'text/markdown')", name="media_type_valid"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    namespace: Mapped[str] = mapped_column(String(64))
    source_key: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    media_type: Mapped[str] = mapped_column(String(100))


class DocumentRevision(Base):
    """Append-only repository contract with exact source and processing snapshots."""

    __tablename__ = "knowledge_document_revisions"
    __table_args__ = (
        UniqueConstraint("document_id", "revision_number", name="uq_knowledge_revision"),
        CheckConstraint("revision_number > 0", name="revision_positive"),
        CheckConstraint("content_sha256 ~ '^[a-f0-9]{64}$'", name="hash_valid"),
        CheckConstraint("normalized_bytes > 0 AND normalized_chars > 0", name="sizes_positive"),
        CheckConstraint("max_chars BETWEEN 64 AND 16384", name="max_chars_valid"),
        CheckConstraint(
            "overlap_chars >= 0 AND overlap_chars <= max_chars / 2", name="overlap_valid"
        ),
        CheckConstraint("chunk_count BETWEEN 1 AND 8192", name="chunk_count_valid"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="RESTRICT")
    )
    revision_number: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64))
    normalized_content: Mapped[str] = mapped_column(Text)
    normalized_bytes: Mapped[int] = mapped_column(Integer)
    normalized_chars: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    media_type: Mapped[str] = mapped_column(String(100))
    parser_version: Mapped[str] = mapped_column(String(64))
    chunker_version: Mapped[str] = mapped_column(String(64))
    config_sha256: Mapped[str] = mapped_column(String(64))
    max_chars: Mapped[int] = mapped_column(Integer)
    overlap_chars: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeChunk(Base):
    """Ordered source slices; parent foreign keys retain tenant and namespace provenance."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("revision_id", "chunk_index", name="uq_knowledge_chunk_index"),
        CheckConstraint("chunk_index >= 0", name="index_nonnegative"),
        CheckConstraint("content_sha256 ~ '^[a-f0-9]{64}$'", name="hash_valid"),
        CheckConstraint(
            "character_start >= 0 AND character_end > character_start", name="range_valid"
        ),
        CheckConstraint("length(content) = character_end - character_start", name="range_length"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_document_revisions.id", ondelete="RESTRICT")
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    section_path: Mapped[list[str]] = mapped_column(ARRAY(Text))
    character_start: Mapped[int] = mapped_column(Integer)
    character_end: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
