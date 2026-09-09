# Phase 5 decision: deterministic knowledge ingestion

Status: accepted implementation decision. Planning baseline: 2026-09-08 Asia/Shanghai;
implementation: 2026-09-09. Scope ends at Phase 5.

## Problem and simplest baseline

SupportOps and DataCopilot need a common, tenant-safe corpus whose source identity,
history and chunks can be reproduced before retrieval experiments. A mutable text
column with fixed windows loses history, permits duplicate races and obscures source
sections. A document framework or an Agent would add dependencies without solving
an uncertainty that exists in this phase.

Workflow First, Agent When Necessary. Deterministic where possible. Agentic where
necessary. Hybrid by design. Models propose. Deterministic systems validate,
authorize and execute. No complexity without measurable value.

## Technology Adoption Gate

All adopted Phase 5 paths are **Deterministic**. No dependency was added or changed.

| Candidate | Concrete problem | Simplest baseline and why insufficient | Complexity and new failure modes | Metric and experiment | Do not adopt / revisit | Replacement boundary |
| --- | --- | --- | --- | --- | --- | --- |
| Strict Pydantic DTOs, explicit UTF-8 scanner | Trusted ownership and reproducible text | Arbitrary metadata/decoding permits injection and environment-dependent content | Small validation/scanner rules; unsupported Markdown constructs, rejected encodings | Unknown ownership fields rejected; encoding/heading/fence golden cases | Reject scanner rules that alter literal source or misstate supported syntax; add formats only with real corpus evidence | ParsedDocument and source-adapter boundary |
| Boundary-aware character chunking | Bounded chunks with source ancestry | Whole document is unbounded; blind windows cross sections and split fitting code blocks | Overlap and fence boundary rules; many tiny sections, oversized fences, chunk-count rejection | Exact ranges/hashes/order repeat; all chunks <= max_chars; literal source coverage; fitting fence matrix | Revisit only if fixed-corpus retrieval tests show truncation, boundary failures or tokenizer limits; reject extra techniques without measurable gain | Versioned pure chunk_document plus ChunkingConfig |
| Three normalized tables with revisions | Identity, historical reproducibility, tenant/collection reads | Overwriting one row destroys past corpus and chunk provenance | Additional joins/storage and migrations; FK/check failures; history growth | CREATED/UPDATED/UNCHANGED; historical rows unchanged; scoped reads including warm identity maps | Reject if atomicity/isolation fail; revisit retention once measured storage costs justify a policy | Focused repository plus detached service result |
| Unique constraints, PostgreSQL upsert and row lock | Same-source concurrent ingestion | Check-then-insert races; unique constraint alone rejects duplicate writers without useful reuse | PostgreSQL-specific insert, lock waiting, timeout/commit ambiguity | Five concurrent requests yield one transition and four UNCHANGED; real unique-constraint and rollback tests | Reject corrupted duplicates; evaluate another strategy if measured contention exceeds an agreed ingestion SLO | acquire_source / append_revision, no job framework |
| 1 MiB input cap, bounded config and 8192 chunks | Bound development CPU/memory/storage work | Unbounded documents or near-total overlap amplify work | Large/fragmented documents rejected | Exact byte cap before decoding; overlap bounds; chunk resource rejection | Raise limits only after a representative memory/latency experiment | Trusted IngestionConfig; no public input override |

One MiB is enough for the small synthetic policies/SQL guides and limits synchronous
in-process work. It is a conservative development limit, not a measured production SLO.
Trusted max_source_bytes may be 1..4 MiB. max_chars is 64..16384, default 1000;
overlap_chars defaults to 100 and is 0..floor(max_chars/2). The ratio bounds duplicated
characters and ensures forward progress. A document may contain at most 8192 chunks.

The baseline is the least additional structure meeting the requested history, provenance
and size requirements. Normalized source storage duplicates some chunk text deliberately:
it permits exact historical coordinate/hash verification without reconstructing overlap.
PostgreSQL TEXT[] holds typed section ancestry; no arbitrary metadata bag or vector column.

## Alternatives deferred

PDF, OCR, general document frameworks, Markdown dependencies, model tokenizers,
semantic/LLM chunking, summaries and model-generated metadata have no Phase 5 evidence
requiring them. Plain text and a documented Markdown subset satisfy this contract.
The parser recognizes ATX H1-H6 and backtick/tilde fences indented up to three spaces.
It does not claim CommonMark compliance: Setext headings, nested block quotes/lists,
HTML blocks and indented code structure are preserved literally but not interpreted.

Embeddings, Qdrant business integration, lexical/dense/hybrid retrieval, RRF, reranking,
query rewriting, HyDE, ContextEngine, Memory, Skills, MCP, A2A, AG-UI and multi-agent
orchestration remain unimplemented. No Agent, LLMGateway or ToolExecutor call occurs.

## Operational semantics and limitations

- Source identity is exact, case-sensitive tenant + namespace + source_key.
  Keys are portable ASCII logical identifiers, never paths or URLs.
- Context is trusted server configuration, not authentication. Repository SQL filters
  tenant and namespace on parents and joins descendants; there is no RLS or public API.
- Strict UTF-8 permits one leading BOM. CRLF/CR become LF; tabs, spaces, Unicode and
  terminal newline presence are preserved. Empty/blank input and C0/C1 controls other
  than tab/LF are rejected. No source is fetched, rendered, imported or executed.
- SHA-256 covers exact normalized UTF-8 bytes, with no title, path, media type, config,
  tenant, UUID or timestamp. Each chunk hashes its exact normalized text slice.
- Latest hash equal means UNCHANGED, including title/media/config changes: existing
  metadata, processing version, chunks and timestamps remain intact. A future explicit
  rebuild/metadata-edit operation must define its own semantics. Hash A -> B -> A
  produces three revisions, preserving chronological history.
- Section boundaries do not overlap. Inside sections, prefer the last paragraph,
  then line, space or tab boundary that makes progress; otherwise hard-slice.
  Overlap is the preceding N characters, reduced where needed to keep a fitting fence
  whole. Oversized fences can be split; no artificial fence delimiters are inserted.
  Coordinates are Python Unicode code-point offsets in normalized text, not raw bytes.
- Parser version text-markdown-v1 and chunker version boundary-chars-v1 are persisted.
  Config fingerprint hashes ASCII chars-v1:max=<N>;overlap=<N>. Stability is only
  promised for equal normalized content and equal parser/chunker versions/config.
- Service owns one Database.transaction per ingestion. Repositories only flush.
  Upsert DO NOTHING does not update unchanged timestamps. Row locking serializes
  same-source version checks. Constraints remain the final guard; no automatic retry.
  The existing default PostgreSQL READ COMMITTED isolation is assumed.
- Errors/cancellation roll back uncommitted work. Connection loss at commit can leave
  the caller uncertain; deliberate retry of the same input resolves by hash/identity.
  No automatic replay, durable job system or production ingestion SLO is claimed.
- Historical immutability is the repository contract; privileged direct SQL can still
  mutate rows. Retention, RLS and DB-role restrictions are future decisions.

## Evaluation and references

Golden/unit and isolated PostgreSQL tests live under backend/tests/knowledge and
backend/tests/integration/test_knowledge.py. Factual corpus results and exact commands
are recorded in evaluation/README.md and progress.md. Synthetic fixtures establish
engineering invariants, not business accuracy or retrieval superiority.

PostgreSQL documents row locking through transaction completion:
[Explicit locking](https://www.postgresql.org/docs/current/explicit-locking.html).
SQLAlchemy exposes PostgreSQL conflict handling:
[INSERT ON CONFLICT](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#insert-on-conflict-upsert).

Revisit deterministic chunking when a fixed, reviewed query set demonstrates a material
retrieval miss caused by boundaries, code fragments or character/token mismatch.
Compare against retained simple windows using Recall@K/MRR/Hit Rate@K plus ingestion
cost, query latency and provenance correctness. Set acceptance thresholds before the
experiment; no dense > lexical or semantic > deterministic ordering is assumed.
