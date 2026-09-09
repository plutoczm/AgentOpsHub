"""Pure source-level metrics; duplicate chunks occupy ranks but cannot earn extra gain."""

import math
from collections.abc import Sequence

from pydantic import Field

from app.knowledge.models import KnowledgeModel


class MetricsAtK(KnowledgeModel):
    """Macro averages over answerable queries; no-answer queries have a separate denominator."""

    k: int = Field(ge=1, le=20)
    answerable_queries: int = Field(ge=0)
    no_answer_queries: int = Field(ge=0)
    hit_rate: float | None
    recall: float | None
    mrr: float | None
    ndcg: float | None
    no_answer_accuracy: float | None


def metrics_at_k(
    rankings: Sequence[Sequence[str]],
    labels: Sequence[frozenset[str]],
    k: int,
) -> MetricsAtK:
    """Calculate HitRate, Recall, reciprocal rank, binary nDCG and empty-result accuracy.

    Cut raw chunk positions to K first. Each source earns relevance only at its first
    occurrence; repeated chunks consume slots. Empty-label queries are excluded from
    positive metrics; no-answer accuracy is the fraction with an empty top-K result.
    """
    if not 1 <= k <= 20 or len(rankings) != len(labels):
        raise ValueError("Invalid metric inputs")
    positives: list[tuple[float, float, float, float]] = []
    empty: list[float] = []
    for ranking, relevant in zip(rankings, labels, strict=True):
        top = ranking[:k]
        if not relevant:
            empty.append(float(not top))
            continue
        seen: set[str] = set()
        hits = 0
        reciprocal = dcg = 0.0
        for position, source in enumerate(top, 1):
            if source in relevant and source not in seen:
                hits += 1
                if not reciprocal:
                    reciprocal = 1 / position
                dcg += 1 / math.log2(position + 1)
            seen.add(source)
        ideal = sum(1 / math.log2(i + 1) for i in range(1, min(k, len(relevant)) + 1))
        positives.append((float(hits > 0), hits / len(relevant), reciprocal, dcg / ideal))
    means = [
        sum(row[i] for row in positives) / len(positives) if positives else None for i in range(4)
    ]
    return MetricsAtK(
        k=k,
        answerable_queries=len(positives),
        no_answer_queries=len(empty),
        hit_rate=means[0],
        recall=means[1],
        mrr=means[2],
        ndcg=means[3],
        no_answer_accuracy=sum(empty) / len(empty) if empty else None,
    )
