import json
import math
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.evaluation.datasets import RetrievalDataset, load_dataset
from app.evaluation.metrics import metrics_at_k
from app.evaluation.retrieval import build_report, deterministic_signature, evaluate
from app.retrieval.models import (
    KnowledgeRetrievalContext,
    KnowledgeSearchRequest,
    RetrievalResult,
)

ROOT = Path(__file__).resolve().parents[3]


def dataset_payload() -> dict[str, object]:
    result: dict[str, object] = json.loads(
        (ROOT / "evaluation/retrieval/datasets/supportops.json").read_text(encoding="utf-8")
    )
    return result


@pytest.mark.parametrize("name", ["supportops", "datacopilot"])
def test_fixed_dataset_schema_and_category_balance(name: str) -> None:
    dataset = load_dataset(ROOT / f"evaluation/retrieval/datasets/{name}.json", ROOT)
    assert len(dataset.documents) == 8 and len(dataset.cases) == 24
    assert len({case.category for case in dataset.cases}) == 7
    assert sum(not case.relevant_sources for case in dataset.cases) == 3


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_id",
        "unknown_source",
        "duplicate_label",
        "bad_category",
        "missing_labels",
        "traversal",
        "duplicate_doc",
    ],
)
def test_invalid_dataset_rejected(mutation: str) -> None:
    payload = json.loads(json.dumps(dataset_payload()))
    if mutation == "duplicate_id":
        payload["cases"][1]["query_id"] = payload["cases"][0]["query_id"]
    elif mutation == "unknown_source":
        payload["cases"][0]["relevant_sources"] = ["not_in_corpus.md"]
    elif mutation == "duplicate_label":
        payload["cases"][0]["relevant_sources"] *= 2
    elif mutation == "bad_category":
        payload["cases"][0]["category"] = "no_answer"
    elif mutation == "missing_labels":
        payload["cases"][0]["relevant_sources"] = []
    elif mutation == "traversal":
        payload["documents"][0]["path"] = "../outside.md"
    else:
        payload["documents"][1] = payload["documents"][0]
    with pytest.raises(ValidationError):
        RetrievalDataset.model_validate_json(json.dumps(payload))


def test_missing_corpus_file_rejected(tmp_path: Path) -> None:
    manifest = tmp_path / "dataset.json"
    manifest.write_text(json.dumps(dataset_payload()), encoding="utf-8")
    with pytest.raises(ValueError, match="Missing"):
        load_dataset(manifest, tmp_path)


@pytest.mark.parametrize("k", [1, 3, 5])
def test_metric_golden_duplicate_chunks_and_multiple_relevant_sources(k: int) -> None:
    result = metrics_at_k([["x", "a", "a", "b"]], [frozenset({"a", "b", "c"})], k)
    if k == 1:
        assert result.hit_rate == result.recall == result.mrr == result.ndcg == 0
    else:
        assert result.hit_rate == 1 and result.mrr == 0.5
        assert result.recall == (1 if k == 3 else 2) / 3
        dcg = 1 / math.log2(3) + (1 / math.log2(5) if k == 5 else 0)
        ideal = 1 + 1 / math.log2(3) + 1 / math.log2(4)
        assert result.ndcg == pytest.approx(dcg / ideal)
    assert result.no_answer_accuracy is None


def test_macro_denominators_and_no_answer_false_positive() -> None:
    result = metrics_at_k(
        [["a", "b"], ["x", "b"], [], ["unrelated"]],
        [frozenset({"a", "b"}), frozenset({"b"}), frozenset(), frozenset()],
        1,
    )
    assert result.answerable_queries == result.no_answer_queries == 2
    assert result.hit_rate == result.mrr == result.ndcg == 0.5
    assert result.recall == 0.25
    assert result.no_answer_accuracy == 0.5
    only_empty = metrics_at_k([[]], [frozenset()], 5)
    assert only_empty.hit_rate is None and only_empty.no_answer_accuracy == 1
    assert metrics_at_k([], [], 5).recall is None


@pytest.mark.parametrize("k", [0, 21])
def test_invalid_metric_k(k: int) -> None:
    with pytest.raises(ValueError):
        metrics_at_k([], [], k)


def test_metric_length_mismatch() -> None:
    with pytest.raises(ValueError):
        metrics_at_k([[]], [], 5)


class FakeRetriever:
    name = "fake-v1"

    def __init__(self) -> None:
        self.calls = 0

    async def search(
        self, context: KnowledgeRetrievalContext, request: KnowledgeSearchRequest
    ) -> RetrievalResult:
        self.calls += 1
        assert request.top_k == 5
        return RetrievalResult(
            retriever=self.name,
            namespace=context.namespace,
            chunks=(),
            duration_ms=float(self.calls),
        )


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["supportops", "datacopilot"])
async def test_evaluator_reproducible_and_backend_independent(name: str) -> None:
    dataset = load_dataset(ROOT / f"evaluation/retrieval/datasets/{name}.json", ROOT)
    context = KnowledgeRetrievalContext(tenant_id=uuid4(), namespace=name)
    retriever = FakeRetriever()
    a = await evaluate(retriever, dataset, context)
    b = await evaluate(retriever, dataset, context)
    assert retriever.calls == 48
    assert deterministic_signature(a) == deterministic_signature(b)
    assert a.latency.samples == 24 and a.latency.mean_ms == 12.5
    assert a.latency.median_ms == 12.5 and a.latency.p95_ms == 23
    assert a.groups[0].metrics[0].hit_rate == 0
    assert a.groups[0].metrics[0].no_answer_accuracy == 1
    assert len(a.groups) == 8


@pytest.mark.anyio
async def test_evaluation_scope_and_ks_rejected_before_calls() -> None:
    dataset = load_dataset(ROOT / "evaluation/retrieval/datasets/supportops.json", ROOT)
    retriever = FakeRetriever()
    with pytest.raises(ValueError):
        await evaluate(
            retriever,
            dataset,
            KnowledgeRetrievalContext(tenant_id=uuid4(), namespace="datacopilot"),
        )
    assert retriever.calls == 0
    for ks in [(), (0,), (21,)]:
        with pytest.raises(ValueError):
            await evaluate(
                retriever,
                dataset,
                KnowledgeRetrievalContext(tenant_id=uuid4(), namespace="supportops"),
                ks=ks,
            )
    with pytest.raises(ValueError):
        build_report(dataset="x", version="1", namespace="x", retriever="x", cases=(), ks=(5,))


def test_benchmark_command_refuses_unowned_database() -> None:
    import os
    import subprocess
    import sys

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AGENTOPSHUB_", "POSTGRES_", "TEST_POSTGRES_"))
    }
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/eval_retrieval.py")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1
    assert (
        result.stdout.strip()
        == "Retrieval benchmark failed; verify isolated setup and test results."
    )
    assert not result.stderr


def test_benchmark_command_rejects_arbitrary_target_arguments() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/dev.py"), "eval-retrieval", "--database", "external"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr
