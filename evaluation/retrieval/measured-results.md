# Measured PostgreSQL FTS baseline: 2026-09-09

Executed with PostgreSQL 17.6, Python 3.12.14, Windows/Conda agentopshub.
Retriever: postgres-fts-simple-cd-v1. simple configuration; plainto_tsquery AND; ts_rank_cd normalization 0.
16 synthetic documents / 48 chunks / 48 queries. Each scenario has 21 answerable and 3 no-answer cases.
Two separate command executions recreated the database; each executed two passes. Stable rankings and
metrics match across passes and across database recreation. Corpus/manifest hashes stayed unchanged.

## Source-level metrics

Values below are fractions, not percentages or confidence. K counts raw chunk slots.

| Dataset | Metric | @1 | @3 | @5 |
| --- | --- | --- | --- | --- |
| supportops | hit_rate | 0.666666667 | 0.666666667 | 0.666666667 |
| supportops | recall | 0.547619048 | 0.619047619 | 0.619047619 |
| supportops | mrr | 0.666666667 | 0.666666667 | 0.666666667 |
| supportops | ndcg | 0.666666667 | 0.629823542 | 0.629823542 |
| datacopilot | hit_rate | 0.619047619 | 0.619047619 | 0.619047619 |
| datacopilot | recall | 0.571428571 | 0.619047619 | 0.619047619 |
| datacopilot | mrr | 0.619047619 | 0.619047619 | 0.619047619 |
| datacopilot | ndcg | 0.619047619 | 0.619047619 | 0.619047619 |
| combined | hit_rate | 0.642857143 | 0.642857143 | 0.642857143 |
| combined | recall | 0.559523810 | 0.619047619 | 0.619047619 |
| combined | mrr | 0.642857143 | 0.642857143 | 0.642857143 |
| combined | ndcg | 0.642857143 | 0.624435581 | 0.624435581 |

No-answer accuracy is 3/3 per scenario, 6/6 combined at every K. These six out-of-domain
negatives are easy; this is not an answerability gate or a production false-positive estimate.

## Category breakdown at K=5

| Dataset | Category | Cases | HitRate | Recall | MRR | nDCG |
| --- | --- | --- | --- | --- | --- | --- |
| supportops | exact | 4 | 0.750000 | 0.750000 | 0.750000 | 0.750000 |
| supportops | ordinary | 4 | 0.500000 | 0.500000 | 0.500000 | 0.500000 |
| supportops | acronym | 3 | 1.000000 | 1.000000 | 1.000000 | 1.000000 |
| supportops | paraphrase | 4 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| supportops | multi_keyword | 3 | 1.000000 | 1.000000 | 1.000000 | 1.000000 |
| supportops | distractor | 3 | 1.000000 | 0.666667 | 1.000000 | 0.742098 |
| datacopilot | exact | 4 | 1.000000 | 1.000000 | 1.000000 | 1.000000 |
| datacopilot | ordinary | 4 | 0.500000 | 0.500000 | 0.500000 | 0.500000 |
| datacopilot | acronym | 3 | 0.666667 | 0.666667 | 0.666667 | 0.666667 |
| datacopilot | paraphrase | 4 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| datacopilot | multi_keyword | 3 | 1.000000 | 1.000000 | 1.000000 | 1.000000 |
| datacopilot | distractor | 3 | 0.666667 | 0.666667 | 0.666667 | 0.666667 |
| combined | exact | 8 | 0.875000 | 0.875000 | 0.875000 | 0.875000 |
| combined | ordinary | 8 | 0.500000 | 0.500000 | 0.500000 | 0.500000 |
| combined | acronym | 6 | 0.833333 | 0.833333 | 0.833333 | 0.833333 |
| combined | paraphrase | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| combined | multi_keyword | 6 | 1.000000 | 1.000000 | 1.000000 | 1.000000 |
| combined | distractor | 6 | 0.833333 | 0.666667 | 0.833333 | 0.704382 |

## Local sequential latency

Latest standalone command, first measured pass; milliseconds. End-to-end validated search includes
pool/session/DB I/O and result mapping. p95 is nearest-rank ceil(0.95*N). These are descriptive
24/48-sample local measurements, not production percentiles, throughput or an index speedup study.

| Dataset | Samples | Mean ms | Median ms | p95 ms |
| --- | --- | --- | --- | --- |
| supportops | 24 | 4.116646 | 4.084250 | 4.609500 |
| datacopilot | 24 | 4.314179 | 4.065650 | 4.977300 |
| combined | 48 | 4.215412 | 4.077650 | 4.699000 |

## Observed errors and interpretation

- Both scenarios: paraphrase HitRate@5=0/4, ordinary wording=2/4. Strict lexical AND does not
  recover conceptual phrasing; ordinary function words also make conjunctions overly restrictive.
- SupportOps exact refund eligibility misses its canonical policy: refund and Eligibility live
  in different Phase 5 chunks. A cancellation chunk mentioning the terms is a distractor.
  This is a chunk representation/conjunction limitation; Dense may not solve it automatically.
- DataCopilot ETL restart misses expanded extract transform load wording; simple does not
  expand acronyms. Some mixed-topic query terms also span separate sections.
- SupportOps distractor HitRate@5=3/3 but Recall@5=2/3: finding one relevant source is not
  equivalent to recovering all labeled sources.
- Non-answerable labels here are deliberately out-of-domain and too few to validate real
  enterprise answerability. Labels are fixed development-time judgments, not independently
  human-adjudicated production ground truth. No Chinese retrieval quality score was measured.

These observations justify evaluating a Dense baseline on the unchanged contracts/data/metrics.
They do not establish Dense superiority or justify automatic Hybrid/RRF/reranking adoption.
Retain FTS and compare gains against latency, cost, provenance and isolation. Simpler stemming,
tokenization or heading representation experiments may address some distinct failure modes.

## Reproducibility identifiers

Corpus SHA-256: 1999e21b7b31f164de4c3ff1a0407a509d5a00ffa4ba350be8705b603d2e5369

| Manifest | Version | SHA-256 |
| --- | --- | --- |
| datacopilot-synthetic-v1 | 1.0.0 | 6f1ce5f9d5a7ca9fd9de496dd1249cd037917c86d489ac3377302eee76f70a77 |
| supportops-synthetic-v1 | 1.0.0 | 13ce9e620102fa8501ba063f2dc25000b84c0df1568c4c9704174e35b86c1694 |

Command: python scripts/dev.py eval-retrieval. Dataset/fixture paths are committed.
The measured working tree was based on Git e508330a00039c1e3e63e1deadb0322c5396b817 with dirty=true;
the final Phase 6 commit identifies the reviewed implementation. Generated JSON remains ignored
under .artifacts/phase6-retrieval.json, including case-level rankings and both timing passes.
Configuration and formulas: README.md. Exact test/quality evidence: ../../progress.md (repository root progress.md).
