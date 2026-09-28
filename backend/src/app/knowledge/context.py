"""Deterministic, bounded assembly of ranked knowledge into untrusted evidence."""

import hashlib
import json
from time import perf_counter
from typing import Self

from pydantic import Field, model_validator

from app.knowledge.models import KnowledgeIngestionContext, KnowledgeModel
from app.retrieval.models import RetrievalResult, RetrievedChunk

CONTEXT_FINGERPRINT_VERSION = "knowledge-evidence-v1"


class KnowledgeContextPolicy(KnowledgeModel):
    """Trusted construction-time retrieval and evidence character budgets."""

    retrieval_top_k: int = Field(default=10, ge=1, le=20)
    max_evidence_chunks: int = Field(default=5, ge=1, le=20)
    max_total_evidence_chars: int = Field(default=6000, ge=1, le=100000)

    @model_validator(mode="after")
    def chunk_budget_fits_retrieval(self) -> Self:
        """Reject a pack limit that can never be reached by this retrieval policy."""
        if self.max_evidence_chunks > self.retrieval_top_k:
            raise ValueError("Evidence chunk limit cannot exceed retrieval_top_k")
        return self


class KnowledgeEvidence(KnowledgeModel):
    """One complete ranked source chunk with provenance and an explicit data boundary."""

    source_key: str = Field(min_length=1, max_length=200, repr=False)
    title: str = Field(min_length=1, max_length=300, repr=False)
    section_path: tuple[str, ...] = Field(repr=False)
    chunk_index: int = Field(ge=0)
    rank: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content: str = Field(min_length=1, max_length=16384, repr=False)

    @classmethod
    def from_chunk(cls, chunk: RetrievedChunk) -> "KnowledgeEvidence":
        """Expose stable source provenance without persistence UUIDs or backend scores."""
        return cls(
            source_key=chunk.source_key,
            title=chunk.title,
            section_path=chunk.section_path,
            chunk_index=chunk.chunk_index,
            rank=chunk.rank,
            content_sha256=chunk.content_sha256,
            content=chunk.content,
        )


class KnowledgeContextPack(KnowledgeModel):
    """Selected evidence and safe deterministic measurements for a single tool result."""

    retriever: str = Field(min_length=1, max_length=100)
    evidence: tuple[KnowledgeEvidence, ...] = Field(max_length=20, repr=False)
    retrieved_count: int = Field(ge=0, le=20)
    omitted_count: int = Field(ge=0, le=20)
    evidence_context_chars: int = Field(ge=0)
    budget_exhausted: bool
    context_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    assembly_duration_ms: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def counts_match(self) -> Self:
        """Keep reported selection and omission counts internally consistent."""
        if self.retrieved_count != len(self.evidence) + self.omitted_count:
            raise ValueError("Evidence counts do not match")
        if self.budget_exhausted != (self.omitted_count > 0):
            raise ValueError("Evidence budget status does not match omitted_count")
        return self


class KnowledgeContextAssemblyError(Exception):
    """Safe semantic failure while converting retrieval results to evidence."""


class KnowledgeContextBudgetExceededError(KnowledgeContextAssemblyError):
    """The highest-ranked complete evidence chunk cannot fit the configured character budget."""


class KnowledgeContextInvariantError(KnowledgeContextAssemblyError):
    """A retriever violated the shared scope, count, or rank contract."""


class KnowledgeContextAssembler:
    """Pack complete chunks in retriever order under a deterministic serialized-char budget."""

    def __init__(self, policy: KnowledgeContextPolicy | None = None) -> None:
        """Snapshot a trusted policy once during application composition."""
        try:
            self.policy = KnowledgeContextPolicy.model_validate(policy or KnowledgeContextPolicy())
        except ValueError:
            raise ValueError("Invalid knowledge context policy") from None

    def assemble(
        self,
        retrieval: RetrievalResult,
        context: KnowledgeIngestionContext,
    ) -> KnowledgeContextPack:
        """Preserve rank/provenance; stop at the first count or whole-chunk budget boundary."""
        started = perf_counter()
        try:
            retrieval = RetrievalResult.model_validate(retrieval)
            context = KnowledgeIngestionContext.model_validate(context)
        except ValueError:
            raise KnowledgeContextInvariantError() from None
        if retrieval.namespace != context.namespace:
            raise KnowledgeContextInvariantError()
        if len(retrieval.chunks) > self.policy.retrieval_top_k:
            raise KnowledgeContextInvariantError()

        selected: list[KnowledgeEvidence] = []
        for chunk in retrieval.chunks:
            if len(selected) >= self.policy.max_evidence_chunks:
                break
            candidate = KnowledgeEvidence.from_chunk(chunk)
            serialized = _canonical_json(
                [evidence.model_dump(mode="json") for evidence in (*selected, candidate)]
            )
            if len(serialized) > self.policy.max_total_evidence_chars:
                if not selected:
                    raise KnowledgeContextBudgetExceededError()
                break
            selected.append(candidate)

        evidence = tuple(selected)
        evidence_chars = len(_canonical_json([item.model_dump(mode="json") for item in evidence]))
        omitted_count = len(retrieval.chunks) - len(evidence)
        fingerprint_payload = {
            "version": CONTEXT_FINGERPRINT_VERSION,
            "retriever": retrieval.retriever,
            "tenant_id": str(context.tenant_id),
            "namespace": context.namespace,
            "policy": self.policy.model_dump(mode="json"),
            "retrieved_count": len(retrieval.chunks),
            "omitted_count": omitted_count,
            "selected": [
                {
                    "rank": item.rank,
                    "source_key": item.source_key,
                    "chunk_index": item.chunk_index,
                    "content_sha256": item.content_sha256,
                }
                for item in evidence
            ],
        }
        fingerprint = hashlib.sha256(
            _canonical_json(fingerprint_payload).encode("utf-8")
        ).hexdigest()
        try:
            return KnowledgeContextPack(
                retriever=retrieval.retriever,
                evidence=evidence,
                retrieved_count=len(retrieval.chunks),
                omitted_count=omitted_count,
                evidence_context_chars=evidence_chars,
                budget_exhausted=omitted_count > 0,
                context_fingerprint=fingerprint,
                assembly_duration_ms=(perf_counter() - started) * 1000,
            )
        except ValueError:
            raise KnowledgeContextInvariantError() from None


def _canonical_json(value: object) -> str:
    """Serialize JSON data with stable key order and no repr or insertion-order dependence."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        raise KnowledgeContextInvariantError() from None
