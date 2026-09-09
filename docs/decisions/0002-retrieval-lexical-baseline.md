# Phase 6 decision: evaluated PostgreSQL FTS lexical baseline

Status: implemented decision. Planning baseline: 2026-09-09 Asia/Shanghai.
Scope ends at Retrieval Evaluation Foundation + Deterministic Lexical Baseline.
Phase 5 ADR and historical migrations remain unchanged.

## Problem, baseline and alternatives

Measure how a small, production-appropriate lexical backend retrieves current knowledge
for SupportOps and DataCopilot before spending effort on embeddings or another service.
The selected implementation is PostgreSQL Full-Text Search, **not BM25**.
All query validation, scope enforcement, matching, ranking, limits and metrics are
deterministic. No LLM, Agent, tool, rewrite or relevance judge is invoked.

| Adoption candidate | Concrete problem / simplest baseline insufficiency | Added complexity / failure modes | Metric / experiment / rejection condition | Replaceability |
| --- | --- | --- | --- | --- |
| PostgreSQL FTS | Substring scanning lacks lexeme matching and relevance ranking; existing PostgreSQL already owns the corpus | Explicit dictionaries, AND semantics, cover-density ranking; lexical/word-form/Chinese misses | Fixed binary labels and HitRate/Recall/MRR/nDCG/no-answer metrics; reject unsafe or non-repeatable results, not low semantic scores | KnowledgeRetriever Protocol; input/result/evaluator stay the same |
| GIN expression index | Unindexed FTS must parse candidate content repeatedly | Index writes and historical storage; exact expression/configuration agreement | Verify real index definition and predicate eligibility; do not claim latency improvement on 16 documents; revisit against representative scale and ingestion cost | New 20260909_02 migration can independently remove index |
| Latest-only SQL | Searching immutable historical chunks leaks obsolete policies | Correlated newer-revision exclusion; query-plan cost | Retired-only term must return zero chunks, current term must return latest revision; reject any stale/foreign result | One scoped SELECT; no current-revision pointer or history rewrite |
| Typed datasets and pure metrics | Ad hoc/manual scoring cannot compare future retrievers reliably | Label quality, duplicate-slot and denominator semantics, local fixture paths | Hand-computed metric tests, missing/duplicate label rejection, repeated rankings/metrics; reject invalid labels or unexplained calculations | Backend-independent evaluate/retrieval result contracts; no evaluation framework dependency |

These rows answer the ten gate questions: problem, simpler baseline, its insufficiency,
deterministic classification, complexity, failure modes, metric, experiment, rejection
condition and component replacement. No runtime dependency was added.

Elasticsearch/OpenSearch introduce another service, synchronization/operational ownership
and analyzers before this corpus needs them. A standalone BM25 library adds tokenization,
in-memory/index lifecycle and tenant isolation work outside the existing store.
Qdrant sparse retrieval adds a client/service and sparse representation/index semantics.
None was benchmarked or claimed inferior; none solves a demonstrated Phase 6 requirement
that PostgreSQL cannot satisfy. A real BM25/tokenizer experiment can be justified later.

## Exact backend choice

Verified existing image and database: PostgreSQL 17.6.
Use to_tsvector('pg_catalog.simple'::regconfig, chunk.content) and
plainto_tsquery('pg_catalog.simple'::regconfig, bound_query).
plainto_tsquery joins lexemes with AND and treats user operators as plain input.
Rank with ts_rank_cd(vector, query, 0): PostgreSQL cover density, default D weights,
no length or 0..1 normalization. Only chunk content participates; document title and
heading ancestry are not concatenated into every chunk.

Tie order: score DESC, source_key COLLATE "C", chunk_index, chunk UUID.
The source/index pair already uniquely orders current chunks inside a namespace;
UUID is a final stable guard. Score is backend-specific, not confidence/probability.
The common result permits any finite score, including future negative similarities.

simple provides lowercasing and default parser tokenization, without English stemming,
synonym expansion, acronym expansion or strong Chinese word segmentation. No Chinese
benchmark score is claimed. This deliberate baseline is language-neutral configuration,
not multilingual semantic understanding.

GIN indexes the same immutable text expression. The index contains historical entries,
but SQL excludes them before results/limit. PostgreSQL deparses the catalog-qualified
regconfig as 'simple'::regconfig; metadata uses that canonical reflected spelling so
Alembic check remains enabled and passes. Migration/query keep explicit pg_catalog.simple.
On tiny scoped data the planner can prefer parent/key indexes; the GIN eligibility test
uses an isolated diagnostic predicate and does not force production plans.

## Safety and version semantics

Trusted KnowledgeRetrievalContext contains tenant_id, namespace and optional request UUID.
Untrusted KnowledgeSearchRequest accepts only query/top_k. Raw query length <=512
characters, conservative surrounding trim, nonblank, valid controls; top_k default 5,
range 1..20. The raw cap applies before trim to prevent oversized padding.

Fixed SQL binds query, tenant UUID, namespace and limit. WHERE constrains tenant and
namespace; NOT EXISTS excludes any revision with a higher-numbered sibling. This uses
statement-snapshot visibility: concurrent commits after statement start affect the next
search. No Python post-filter is used for authorization or stale-version exclusion.
Read sessions never commit. Output provenance is detached and validated.

Public search endpoint, knowledge_search tool registration, answer generation,
embedding/Dense/Qdrant/Hybrid/RRF/reranking are absent.
Safe logs contain fixed events, trusted correlation/namespace, retriever, top-K, count
and monotonic time. No raw query, source title/key, chunk body or database error is logged.

Phase 5 hashes normalized content; title/media/config-only changes remain UNCHANGED.
Phase 6 does not redefine those hashes or abuse revisions as index versions.
Later parser/chunker changes require an explicit rebuild/reindex contract; metadata-only
versioning also needs an independent decision. Historical raw rows are not search defaults.

## Evaluation and adoption evidence

Fixed before first measurement: 8 documents and 24 queries per scenario, with 4 exact,
4 ordinary, 3 acronym, 4 paraphrase, 3 multi-keyword, 3 distractor and 3 no-answer queries.
Labels and rationales are manually specified, binary, source-level and UUID-independent.
The original six Phase 5 fixtures are referenced unchanged; ten new fixtures remain
under evaluation/retrieval/fixtures. All corpus data passes through Phase 5 ingestion.

At K=1/3/5, cut raw chunk positions first; duplicate sources consume rank slots but earn
gain once. Positive metrics exclude no-answer cases; no-answer accuracy measures an
empty returned list among empty-label cases. MRR is truncated at K; nDCG uses binary
first-source gain and ideal min(K, number of relevant sources).
See evaluation/retrieval/README.md for formulas, measured tables and caveats.

Observed @5: SupportOps HitRate 14/21, Recall 13/21; DataCopilot both 13/21.
Paraphrases: 0/4 in each; ordinary wording: 2/4 in each. These failures justify **evaluating**
Dense against the same labels, contracts and metrics, not assuming it wins.
Missing acronym expansion, strict AND on conversational words and terms split between
sections also suggest simpler lexical/context representation experiments. Dense alone
may not solve those. No fixture/label was edited to raise scores after first measurement.

Six no-answer queries returned empty; these easy out-of-domain negatives do not establish
an answerability gate or production false-positive rate. The tiny English synthetic
corpus, manual judgments, sequential local timings and lack of customer traffic limit
external validity. Freeze this v1 benchmark for comparisons; expand future dataset
versions transparently rather than tuning to the observed scores.

Phase 7 may evaluate one justified embedding backend/model and Qdrant Dense implementation.
Keep lexical results, use identical evaluation, compare category errors and operational
cost. Do not adopt Dense if improvement on the reviewed failures does not justify added
cost/latency/dependencies or if isolation/provenance regress. Hybrid/RRF is a later decision.

## Primary references

- [PostgreSQL 17 query/rank behavior](https://www.postgresql.org/docs/17/textsearch-controls.html)
- [PostgreSQL FTS expression indexes](https://www.postgresql.org/docs/17/textsearch-tables.html)
- [PostgreSQL simple dictionary](https://www.postgresql.org/docs/17/textsearch-dictionaries.html)
