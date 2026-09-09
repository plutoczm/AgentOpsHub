"""Trusted scope, untrusted search request and application-owned ranked results."""

from typing import Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.knowledge.models import KnowledgeIngestionContext, KnowledgeModel


class KnowledgeRetrievalContext(KnowledgeIngestionContext):
    """Trusted caller ownership; never inferred from a query or returned document."""


class KnowledgeSearchRequest(KnowledgeModel):
    """Bounded untrusted text, without SQL, ownership or revision controls."""

    query: str = Field(min_length=1, max_length=512, repr=False)
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value: object) -> object:
        """Reject oversized raw input, then strip edges without rewriting search terms."""
        if not isinstance(value, str):
            return value
        if len(value) > 512 or any(
            (ord(c) < 32 and c not in "\t\n\r")
            or 127 <= ord(c) <= 159
            or 0xD800 <= ord(c) <= 0xDFFF
            for c in value
        ):
            raise ValueError("Invalid query text")
        return value.strip()


class RetrievedChunk(KnowledgeModel):
    """One ranked internal source slice; score is backend-specific, never confidence."""

    document_id: UUID
    revision_id: UUID
    chunk_id: UUID
    source_key: str = Field(min_length=1, max_length=200, repr=False)
    title: str = Field(min_length=1, max_length=300, repr=False)
    namespace: str
    chunk_index: int = Field(ge=0)
    section_path: tuple[str, ...] = Field(repr=False)
    content: str = Field(min_length=1, max_length=16384, repr=False)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    character_start: int = Field(ge=0)
    character_end: int = Field(gt=0)
    score: float = Field(allow_inf_nan=False)
    rank: int = Field(ge=1)

    @model_validator(mode="after")
    def source_range(self) -> Self:
        """Keep content and normalized source coordinates consistent."""
        if self.character_end - self.character_start != len(self.content):
            raise ValueError("Invalid retrieval provenance")
        return self


class RetrievalResult(KnowledgeModel):
    """Detached ordered results; rank semantics are shared across future backends."""

    retriever: str
    namespace: str
    chunks: tuple[RetrievedChunk, ...] = Field(repr=False)
    duration_ms: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered_scope(self) -> Self:
        """Reject duplicate chunks, mixed namespaces and non-contiguous ranks."""
        if len(self.chunks) > 20 or len({c.chunk_id for c in self.chunks}) != len(self.chunks):
            raise ValueError("Invalid retrieval result count")
        if any(c.rank != i or c.namespace != self.namespace for i, c in enumerate(self.chunks, 1)):
            raise ValueError("Invalid retrieval result scope or ranks")
        return self
