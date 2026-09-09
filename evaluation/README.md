# Evaluation boundary

Phase 5 implements a deterministic ingestion evaluation baseline through committed
synthetic fixtures and tests. A separate executable evaluation distribution and
retrieval benchmarks remain planned.

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

## Planned retrieval experiments

Phase 6 compares a lexical baseline and dense baseline with explicit Qdrant integration.
Phase 7 evaluates hybrid fusion/RRF, retaining each simpler baseline. Reranking and
semantic/token-aware chunking need separate measured justification.
Do not assume dense > lexical, hybrid > dense, reranking > hybrid, or semantic >
deterministic chunking.

Use a fixed versioned corpus/query split and retain raw outputs. Measure Recall@K,
MRR, Hit Rate@K, latency, ingestion/storage cost and provenance/tenant correctness.
Agree thresholds before selecting extra complexity. Context Engineering begins only
after retrieval baselines justify it.

The future evaluation package may use evaluation/pyproject.toml,
evaluation/src/agentopshub_eval and evaluation/tests, calling public internal
retrieval/Agent contracts without importing persistence internals.
