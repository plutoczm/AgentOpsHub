"""Strict contracts separating trusted ownership from untrusted source content."""

import hashlib
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class KnowledgeModel(BaseModel):
    """Immutable values with forbidden extras and privacy-safe representations."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class KnowledgeIngestionContext(KnowledgeModel):
    """Trusted server context; never constructed from document metadata."""

    tenant_id: UUID
    namespace: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    request_id: UUID | None = None


class DocumentInput(KnowledgeModel):
    """Untrusted bytes and source identity, without persistence or ownership fields."""

    source_key: str = Field(min_length=1, max_length=200, repr=False)
    title: str = Field(min_length=1, max_length=300, repr=False)
    media_type: str = Field(min_length=1, max_length=100, repr=False)
    content: bytes = Field(repr=False)

    @field_validator("source_key")
    @classmethod
    def source_identity(cls, value: str) -> str:
        """Allow portable logical identifiers, never paths or URLs."""
        import re

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", value) or ".." in value:
            raise ValueError("Invalid logical source key")
        return value

    @field_validator("title")
    @classmethod
    def title_text(cls, value: str) -> str:
        """Reject blank titles and control characters without echoing them."""
        if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Invalid document title")
        return value


class ChunkingConfig(KnowledgeModel):
    """Character-based baseline with bounded duplication and guaranteed progress."""

    max_chars: int = Field(default=1000, ge=64, le=16384)
    overlap_chars: int = Field(default=100, ge=0)

    @model_validator(mode="after")
    def overlap_bound(self) -> Self:
        """Bound overlap to half a chunk to avoid pathological storage amplification."""
        if self.overlap_chars > self.max_chars // 2:
            raise ValueError("Overlap must be at most half max_chars")
        return self

    @property
    def fingerprint(self) -> str:
        """Hash a canonical, versioned configuration serialization."""
        value = f"chars-v1:max={self.max_chars};overlap={self.overlap_chars}"
        return hashlib.sha256(value.encode("ascii")).hexdigest()


class IngestionConfig(KnowledgeModel):
    """Trusted per-service bounds; default source cap is one MiB."""

    max_source_bytes: int = Field(default=1048576, ge=1, le=4194304)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)


class Section(KnowledgeModel):
    """Heading ancestry and half-open offsets in normalized source text."""

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    path: tuple[str, ...] = Field(default=(), repr=False)


class ParsedDocument(KnowledgeModel):
    """Normalized content plus explicit structure; no rendered or executed Markdown."""

    text: str = Field(repr=False)
    media_type: str
    sections: tuple[Section, ...]
    fences: tuple[tuple[int, int], ...] = ()
    parser_version: str = "text-markdown-v1"


class ChunkDraft(KnowledgeModel):
    """Deterministic chunk content and source coordinates before persistence."""

    chunk_index: int = Field(ge=0)
    content: str = Field(min_length=1, repr=False)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    section_path: tuple[str, ...] = Field(repr=False)
    character_start: int = Field(ge=0)
    character_end: int = Field(gt=0)


class IngestionStatus(StrEnum):
    """Explicit state transition outcomes."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


class IngestionResult(KnowledgeModel):
    """Detached safe result validated before commit."""

    status: IngestionStatus
    document_id: UUID
    revision_id: UUID
    revision_number: int = Field(ge=1)
    namespace: str
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    chunk_count: int = Field(ge=1)
    duration_ms: float = Field(ge=0, allow_inf_nan=False)
