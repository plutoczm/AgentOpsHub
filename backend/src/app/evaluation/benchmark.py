"""Local synthetic benchmark composition; generic ingestion, retrieval and evaluation."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

from app.db.session import Database
from app.evaluation.datasets import load_dataset
from app.evaluation.retrieval import (
    EvaluationReport,
    build_report,
    deterministic_signature,
    evaluate,
)
from app.knowledge.models import (
    ChunkingConfig,
    DocumentInput,
    KnowledgeIngestionContext,
    KnowledgeModel,
)
from app.repositories.tenant import TenantRepository
from app.retrieval.models import KnowledgeRetrievalContext
from app.retrieval.postgres import PostgresFTSRetriever
from app.services.knowledge import KnowledgeIngestionService


class DatasetFingerprint(KnowledgeModel):
    """Versioned manifest bytes and factual fixture counts."""

    name: str
    version: str
    sha256: str
    documents: int
    queries: int


class BenchmarkEvidence(KnowledgeModel):
    """Small reproducibility record; generated output stays in ignored artifacts."""

    retriever: str
    postgres_version: str
    text_search_config: str = "pg_catalog.simple"
    ranking: str = "ts_rank_cd normalization=0"
    ks: tuple[int, ...] = (1, 3, 5)
    chunking: ChunkingConfig
    corpus_sha256: str
    datasets: tuple[DatasetFingerprint, ...]
    unchanged_documents: int
    repeat_identical: bool
    reports: tuple[EvaluationReport, ...]
    repeat_reports: tuple[EvaluationReport, ...]


async def run_benchmark(database: Database, root: Path) -> BenchmarkEvidence:
    """Create a synthetic tenant, ingest manifests and evaluate twice in the supplied test DB."""
    manifests = sorted((root / "evaluation/retrieval/datasets").glob("*.json"))
    if not manifests:
        raise ValueError("No benchmark datasets")
    datasets = [load_dataset(path, root) for path in manifests]
    if len({d.namespace for d in datasets}) != len(datasets):
        raise ValueError("Duplicate benchmark namespace")
    async with database.transaction() as session:
        tenant_id = (
            await TenantRepository(session).create(
                slug=f"retrieval-{uuid4().hex}",
                name="Synthetic retrieval benchmark",
            )
        ).id
        version = str(await session.scalar(text("SHOW server_version")))
    service = KnowledgeIngestionService(database)
    unchanged = 0
    corpus: list[tuple[str, str, str]] = []
    for dataset in datasets:
        context = KnowledgeIngestionContext(tenant_id=tenant_id, namespace=dataset.namespace)
        for document in dataset.documents:
            raw = (root / document.path).read_bytes()
            source = DocumentInput(
                source_key=document.source_key,
                title=document.title,
                media_type="text/markdown",
                content=raw,
            )
            created = await service.ingest(source, context)
            repeated = await service.ingest(source, context)
            if repeated.status.value != "unchanged" or repeated.revision_id != created.revision_id:
                raise ValueError("Benchmark ingestion was not idempotent")
            unchanged += 1
            corpus.append((dataset.namespace, document.source_key, created.content_sha256))
    retriever = PostgresFTSRetriever(database)
    reports: list[EvaluationReport] = []
    repeats: list[EvaluationReport] = []
    for dataset in datasets:
        context_r = KnowledgeRetrievalContext(tenant_id=tenant_id, namespace=dataset.namespace)
        first = await evaluate(retriever, dataset, context_r)
        second = await evaluate(retriever, dataset, context_r)
        if deterministic_signature(first) != deterministic_signature(second):
            raise ValueError("Benchmark rankings or metrics changed across repeated runs")
        reports.append(first)
        repeats.append(second)
    for group in (reports, repeats):
        combined = build_report(
            dataset="combined-synthetic-v1",
            version="1.0.0",
            namespace="combined",
            retriever=retriever.name,
            cases=tuple(case for report in group for case in report.cases),
            ks=(1, 3, 5),
        )
        group.append(combined)
    return BenchmarkEvidence(
        retriever=retriever.name,
        postgres_version=version,
        chunking=ChunkingConfig(),
        corpus_sha256=hashlib.sha256(
            json.dumps(sorted(corpus), separators=(",", ":")).encode()
        ).hexdigest(),
        datasets=tuple(
            DatasetFingerprint(
                name=dataset.name,
                version=dataset.version,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                documents=len(dataset.documents),
                queries=len(dataset.cases),
            )
            for path, dataset in zip(manifests, datasets, strict=True)
        ),
        unchanged_documents=unchanged,
        repeat_identical=True,
        reports=tuple(reports),
        repeat_reports=tuple(repeats),
    )
