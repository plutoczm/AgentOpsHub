"""One small backend-independent seam for retrieval and evaluation."""

from typing import Protocol

from app.retrieval.models import KnowledgeRetrievalContext, KnowledgeSearchRequest, RetrievalResult


class KnowledgeRetriever(Protocol):
    """Future dense retrievers must preserve the same input, scope and result contract."""

    @property
    def name(self) -> str:
        """Return the versioned backend/configuration identity."""
        ...

    async def search(
        self,
        context: KnowledgeRetrievalContext,
        request: KnowledgeSearchRequest,
    ) -> RetrievalResult:
        """Search a bounded current corpus under trusted tenant/namespace ownership."""
        ...
