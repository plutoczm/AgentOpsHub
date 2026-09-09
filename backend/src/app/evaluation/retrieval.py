"""Backend-independent retrieval evaluator with repeatable rank/label semantics."""

import math
from collections.abc import Sequence
from statistics import mean, median

from pydantic import Field

from app.evaluation.datasets import QueryCategory, RetrievalDataset
from app.evaluation.metrics import MetricsAtK, metrics_at_k
from app.knowledge.models import KnowledgeModel
from app.retrieval.models import KnowledgeRetrievalContext, KnowledgeSearchRequest, RetrievalResult
from app.retrieval.protocols import KnowledgeRetriever


class RankedSource(KnowledgeModel):
    """Stable provenance identity for comparisons across database recreation."""

    source_key: str
    section_path: tuple[str, ...]
    chunk_index: int
    content_sha256: str


class CaseRun(KnowledgeModel):
    """Deliberate synthetic evaluation output; no raw query or retrieved body."""

    query_id: str
    category: QueryCategory
    relevant_sources: tuple[str, ...]
    ranking: tuple[RankedSource, ...]
    duration_ms: float = Field(ge=0, allow_inf_nan=False)


class LatencySummary(KnowledgeModel):
    """Local sequential wall-clock samples, not production latency estimates."""

    samples: int
    mean_ms: float
    median_ms: float
    p95_ms: float


class EvaluationGroup(KnowledgeModel):
    """Metrics for all queries or a fixed category."""

    category: str
    query_count: int
    metrics: tuple[MetricsAtK, ...]


class EvaluationReport(KnowledgeModel):
    """Normalized baseline output consumable by future dense/hybrid comparison."""

    dataset: str
    dataset_version: str
    namespace: str
    retriever: str
    query_count: int
    groups: tuple[EvaluationGroup, ...]
    latency: LatencySummary
    cases: tuple[CaseRun, ...]


def summarize(name: str, cases: Sequence[CaseRun], ks: tuple[int, ...]) -> EvaluationGroup:
    """Compute one group's metrics with the exact same evaluator definitions."""
    return EvaluationGroup(
        category=name,
        query_count=len(cases),
        metrics=tuple(
            metrics_at_k(
                [tuple(item.source_key for item in case.ranking) for case in cases],
                [frozenset(case.relevant_sources) for case in cases],
                k,
            )
            for k in ks
        ),
    )


def build_report(
    *,
    dataset: str,
    version: str,
    namespace: str,
    retriever: str,
    cases: tuple[CaseRun, ...],
    ks: tuple[int, ...],
) -> EvaluationReport:
    """Summarize normalized case runs, preserving timing separately from deterministic metrics."""
    if not cases or not ks or len(set(ks)) != len(ks) or any(not 1 <= k <= 20 for k in ks):
        raise ValueError("Invalid evaluation inputs")
    if len({case.query_id for case in cases}) != len(cases):
        raise ValueError("Duplicate evaluation query_id")
    samples = sorted(case.duration_ms for case in cases)
    groups = [summarize("all", cases, ks)]
    for category in QueryCategory:
        selected = tuple(case for case in cases if case.category is category)
        if selected:
            groups.append(summarize(category.value, selected, ks))
    return EvaluationReport(
        dataset=dataset,
        dataset_version=version,
        namespace=namespace,
        retriever=retriever,
        query_count=len(cases),
        groups=tuple(groups),
        cases=cases,
        latency=LatencySummary(
            samples=len(samples),
            mean_ms=mean(samples),
            median_ms=median(samples),
            p95_ms=samples[math.ceil(0.95 * len(samples)) - 1],
        ),
    )


async def evaluate(
    retriever: KnowledgeRetriever,
    dataset: RetrievalDataset,
    context: KnowledgeRetrievalContext,
    *,
    ks: tuple[int, ...] = (1, 3, 5),
) -> EvaluationReport:
    """Evaluate an already ingested corpus; no database or model implementation is imported."""
    dataset = RetrievalDataset.model_validate(dataset)
    context = KnowledgeRetrievalContext.model_validate(context)
    if (
        dataset.namespace != context.namespace
        or not ks
        or len(set(ks)) != len(ks)
        or any(not 1 <= k <= 20 for k in ks)
    ):
        raise ValueError("Invalid evaluation scope or K values")
    known = {document.source_key for document in dataset.documents}
    cases: list[CaseRun] = []
    for case in dataset.cases:
        result = RetrievalResult.model_validate(
            await retriever.search(context, KnowledgeSearchRequest(query=case.query, top_k=max(ks)))
        )
        if result.namespace != context.namespace or result.retriever != retriever.name:
            raise ValueError("Retriever identity or namespace mismatch")
        if len(result.chunks) > max(ks) or any(c.source_key not in known for c in result.chunks):
            raise ValueError("Retriever returned undeclared corpus or excessive results")
        cases.append(
            CaseRun(
                query_id=case.query_id,
                category=case.category,
                relevant_sources=case.relevant_sources,
                ranking=tuple(
                    RankedSource(
                        source_key=c.source_key,
                        section_path=c.section_path,
                        chunk_index=c.chunk_index,
                        content_sha256=c.content_sha256,
                    )
                    for c in result.chunks
                ),
                duration_ms=result.duration_ms,
            )
        )
    return build_report(
        dataset=dataset.name,
        version=dataset.version,
        namespace=context.namespace,
        retriever=retriever.name,
        cases=tuple(cases),
        ks=ks,
    )


def deterministic_signature(report: EvaluationReport) -> dict[str, object]:
    """Compare all output except explicitly nondeterministic timing fields."""
    result = report.model_dump(mode="json", exclude={"latency", "cases"})
    result["cases"] = [
        case.model_dump(mode="json", exclude={"duration_ms"}) for case in report.cases
    ]
    return result
