"""Typed, inspectable synthetic dataset contracts with stable source labels."""

from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, field_validator, model_validator

from app.knowledge.models import DocumentInput, KnowledgeModel
from app.retrieval.models import KnowledgeSearchRequest


class QueryCategory(StrEnum):
    """Manually assigned query categories, independent of observed retrieval scores."""

    EXACT = "exact"
    ORDINARY = "ordinary"
    ACRONYM = "acronym"
    PARAPHRASE = "paraphrase"
    MULTI_KEYWORD = "multi_keyword"
    DISTRACTOR = "distractor"
    NO_ANSWER = "no_answer"


class CorpusDocument(KnowledgeModel):
    """A repository-relative synthetic source path and stable logical identifier."""

    source_key: str
    title: str = Field(repr=False)
    path: str

    @model_validator(mode="after")
    def source_contract(self) -> Self:
        """Validate identity through the accepted ingestion contract and forbid traversal."""
        DocumentInput(
            source_key=self.source_key,
            title=self.title,
            media_type="text/markdown",
            content=b"validation",
        )
        if self.path.startswith(("/", "\\")) or "\\" in self.path or ":" in self.path:
            raise ValueError("Invalid corpus path")
        if any(part in ("", ".", "..") for part in self.path.split("/")):
            raise ValueError("Invalid corpus path")
        if not self.path.endswith(".md"):
            raise ValueError("Corpus fixture must be Markdown")
        return self


class RetrievalCase(KnowledgeModel):
    """Binary source relevance; database UUIDs never enter ground truth."""

    query_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_-]+$")
    query: str = Field(repr=False)
    category: QueryCategory
    relevant_sources: tuple[str, ...]
    rationale: str = Field(min_length=1, max_length=1000, repr=False)

    @field_validator("query")
    @classmethod
    def query_contract(cls, value: str) -> str:
        """Use the identical query validation as production retrieval."""
        return KnowledgeSearchRequest(query=value).query

    @model_validator(mode="after")
    def binary_labels(self) -> Self:
        """No-answer means exactly an empty label set; duplicate labels are invalid."""
        if len(set(self.relevant_sources)) != len(self.relevant_sources):
            raise ValueError("Duplicate relevance label")
        if (self.category is QueryCategory.NO_ANSWER) != (not self.relevant_sources):
            raise ValueError("No-answer category and relevance labels disagree")
        return self


class RetrievalDataset(KnowledgeModel):
    """Versioned local corpus and cases with referentially valid binary labels."""

    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=32)
    namespace: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    description: str = Field(min_length=1, max_length=1000)
    documents: tuple[CorpusDocument, ...] = Field(min_length=1, max_length=100)
    cases: tuple[RetrievalCase, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def references(self) -> Self:
        """Reject duplicate identities and labels pointing outside the declared corpus."""
        keys = {d.source_key for d in self.documents}
        if len(keys) != len(self.documents) or len({d.path for d in self.documents}) != len(
            self.documents
        ):
            raise ValueError("Duplicate corpus identity")
        if len({c.query_id for c in self.cases}) != len(self.cases):
            raise ValueError("Duplicate query_id")
        if any(not set(c.relevant_sources) <= keys for c in self.cases):
            raise ValueError("Relevance label references missing source")
        return self


def load_dataset(path: Path, root: Path) -> RetrievalDataset:
    """Load an explicitly selected local manifest and verify every fixture stays under root."""
    dataset = RetrievalDataset.model_validate_json(path.read_bytes())
    root = root.resolve()
    for document in dataset.documents:
        source = (root / document.path).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            raise ValueError("Missing or out-of-root corpus fixture")
    return dataset
