# Evaluation boundary

Phase 5 implements a deterministic ingestion evaluation baseline through committed
synthetic fixtures and tests. Phase 6 now adds a focused retrieval evaluator and PostgreSQL FTS lexical baseline;
a separate evaluation distribution and full Agent Harness remain planned.

Run from the accepted isolated Python/Conda environment:

```text
python -m pytest backend/tests/knowledge -q
python scripts/dev.py test-integration
```

The integration runner creates a private PostgreSQL database, runs migrations and
the entire test suite, then checks actual HTTP liveness/readiness before and after
stopping its own database. No model key, warehouse access or external corpus is needed.

| Synthetic corpus | Documents | Default chunks | Verified ingestion behavior |
| --- | --- | --- | --- |
| SupportOps | 3 | 9 | CREATED then UNCHANGED; generic pipeline |
| DataCopilot | 3 | 9 | CREATED then UNCHANGED; generic pipeline |
| Total | 6 | 18 | 6 revisions, no duplicates on repeat; complete provenance |

Every fixture produces three sections/chunks with default max_chars=1000 and
overlap_chars=100. Actual per-document SHA-256, chunk counts and repeat/provenance
results are written to ignored .artifacts/phase5-ingestion.json by the PostgreSQL test.
Raw suite/coverage/migration/HTTP artifacts retain the existing phase1-* filenames.
Exact final test counts and coverage are recorded in progress.md.

Tests verify repeated normalization/chunks/order/hashes/ranges, golden exact windows
and heading ancestry, Unicode/encoding/size errors, fence content preservation,
source identity, revision history, unchanged timestamps, tenant/namespace SQL isolation,
real uniqueness constraints, same-source concurrency, partial-write rollback,
fixed safe errors, monotonic timing and body-free raw/JSON logs.
LLMGateway, AgentRuntime and ToolExecutor are patched to fail if ingestion invokes them.

These are reproducibility and boundary checks on fictional documents, not business
accuracy scores, service SLOs, retrieval results or remote scenario interoperability.

## Phase 6 retrieval evaluation (implemented)

Run `python scripts/dev.py eval-retrieval` for an isolated PostgreSQL synthetic benchmark.
It ingests 16 documents (8 per scenario), evaluates 48 labeled queries (24 per scenario),
reports source-level HitRate/Recall/MRR/binary nDCG at 1/3/5 and no-answer accuracy,
and repeats rankings/metrics to verify determinism. This is PostgreSQL FTS, not BM25.

[Dataset and metric contract](retrieval/README.md) describes raw chunk-slot deduplication,
denominators and fixture validation. [Measured results](retrieval/measured-results.md)
records actual aggregate/category metrics and local latency.

Observed @5 HitRate: SupportOps 14/21, DataCopilot 13/21; Recall 13/21 each.
Both paraphrase groups are 0/4 and ordinary wording groups 2/4. Six easy no-answer queries
returned empty. These are synthetic baseline findings, not production relevance/SLO claims.
Phase 5's original six-document/18-chunk ingestion regression above remains unchanged.

## Planned comparisons

Phase 7 evaluates Dense Retrieval + Qdrant using the SAME retrieval contracts, frozen
datasets and pure metric implementation. An embedding abstraction and model/provider
choice require explicit adoption evidence. Keep the lexical baseline; no Hybrid/RRF yet.
Phase 8 analyzes errors and decides whether fusion adds measurable value. Reranking,
Context Engineering and further Agent capabilities follow only after evidence.

Do not assume dense > lexical, hybrid > dense, reranking > hybrid or semantic >
deterministic chunking. Rebuild/reindex and metadata-only version semantics remain
explicit future decisions; do not silently change Phase 5 hashes or chunk configuration.
