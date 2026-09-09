# Retrieval evaluation v1: synthetic benchmark

This is a focused deterministic retrieval evaluator, not the future Agent Harness.
Data is synthetic and non-sensitive. Labels and rationales are manually specified
before the first benchmark run; no runtime LLM relevance judgments or corpus tuning.
These development-time labels have not received independent human adjudication.

## Run and reproduce

```text
python scripts/dev.py eval-retrieval
python scripts/dev.py test-integration
```

Use the existing Conda/process-local workflow. eval-retrieval creates a unique private
PostgreSQL Compose project with temporary storage, applies migrations, creates one
synthetic tenant, ingests all fixtures through KnowledgeIngestionService and evaluates
each dataset twice. It never uses an ordinary application/production database or external
API and cleans only its own resources. No GPU, model key or embedding model is needed.

Generated .artifacts/phase6-retrieval.json contains dataset/version and byte hashes,
normalized corpus hash, PostgreSQL version, retriever/config, K values, chunk config,
Git HEAD/dirty status, per-case stable rankings, category metrics and both timing runs.
It deliberately omits raw query and retrieved content. .artifacts/phase6-integration-benchmark.json
is the equivalent integration evidence. Neither generated dump is committed.
Dirty=true accurately records pre-commit execution; the final implementation SHA is
in Git history and the final report. Stable ranks use source/section/index/content hash,
not generated UUIDs; ordinary timing differences are excluded from repeat comparisons.

## Corpus and labels

Two datasets: SupportOps and DataCopilot, each 8 documents and 24 queries.
Each references three unchanged examples/knowledge fixtures and five new files in
fixtures/<namespace>. Thus 16 documents, 48 default Phase 5 chunks and 48 queries.
Every document uses the same ingestion, repository and retrieval engine.

Each case has query_id, text, category, relevant_sources and an inspectable rationale.
Dataset supplies version and namespace; execution tenant is trusted context, never labels.
Duplicate IDs, invalid/no-answer label combinations and missing source references fail
validation. Fixture paths must exist under the explicitly selected repository root.

Per scenario: exact terminology 4; ordinary wording 4; acronym 3; paraphrase/synonym 4;
multi-keyword 3; distractor-heavy 3; no-answer 3.
Binary relevance means the source materially addresses the labeled topic, not that any
keyword overlap is relevant. Some distractor sources mention a topic without its policy.
No DB UUID, score threshold or model judgment defines truth.

## Metrics

Let R be the set of relevant source keys and C_K the first K raw returned chunks.
Duplicate chunks from a source retain their rank positions but count relevance once.
No source-level backfill is performed beyond the raw top-K result window.

For answerable queries (R nonempty):

- HitRate@K: mean of 1 when any source in C_K belongs to R, otherwise 0.
- Recall@K: mean |unique sources(C_K) intersect R| / |R|.
- MRR@K: mean 1/r for the first relevant chunk rank r<=K; 0 if none.
- nDCG@K: binary gain 1 only at a relevant source's first occurrence; DCG is the sum
  of gain/log2(rank+1). IDCG sums the first min(K,|R|) ideal gains. Average DCG/IDCG.

No-answer queries are excluded from those four denominators. No-Answer Accuracy@K
is the fraction of empty-label queries whose C_K is empty; related-looking chunks
still count as a false positive. Groups without the applicable query type report null,
not a fabricated zero. Combined metrics macro-average queries across both scenarios.
No separate chunk-level judgments or Precision@K are introduced.

K values are 1/3/5; one top-5 search supports all cutoffs. Score is backend-specific;
metrics compare rank and labels, never raw FTS versus dense score scales.

## Measured baseline

See measured-results.md for the factual tables and failure analysis from the executed
PostgreSQL 17.6 baseline. Successful engineering acceptance means valid labels, correct
metrics, safe/current retrieval and repeatability, not a target such as 95% accuracy.

Local latency uses perf_counter around each validated retrieval, including session/
database I/O and result mapping. Mean, median and nearest-rank p95 use the recorded
sequential samples; the first measured query may include warmup effects. The second
pass is shown separately in artifacts. There is no production percentile or throughput
claim, no cross-platform extrapolation and no GIN speedup claim for this small corpus.

## Future comparison boundary

app.evaluation.retrieval.evaluate depends on KnowledgeRetriever and common DTOs.
A Dense retriever can reuse this exact corpus, labels, metrics and category breakdown.
Phase 7 must justify provider/model and Qdrant adoption independently; retain lexical.
Hybrid/RRF, reranking, query rewriting, LLM-as-judge and answer generation are deferred.

The English synthetic data and six easy out-of-domain negatives are limited evidence.
simple does not supply strong Chinese segmentation, stemming or conceptual matching.
Human relevance judgments require review before extending to real business datasets.
Parser/chunker or metadata-only changes require explicit Phase 5 rebuild/version decisions;
do not silently change corpus versions or content hashing to accommodate index changes.
