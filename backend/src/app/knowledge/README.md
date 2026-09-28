# Internal deterministic knowledge ingestion

Use KnowledgeIngestionService from app.services.knowledge with the application-owned
Database and a trusted KnowledgeIngestionContext. This is a local Python service,
not an HTTP upload endpoint or Agent tool.

```python
context = KnowledgeIngestionContext(tenant_id=trusted_tenant_id, namespace="supportops")
document = DocumentInput(
    source_key="refund_policy.md",
    title="Synthetic refund policy",
    media_type="text/markdown",
    content=trusted_local_file_bytes,
)
result = await KnowledgeIngestionService(database).ingest(document, context)
```

DocumentInput forbids tenant/namespace, IDs, revision numbers and timestamps.
Callers read explicitly selected local files themselves; this service never opens
paths, fetches URLs, runs SQL, evaluates Markdown or invokes models/tools.

Phase 7A's separate `KnowledgeContextAssembler` consumes retrieval results; it does not
change ingestion or revision semantics. Its bounded evidence and Agent integration are
described in the root [Phase 7A section](../../../../README.md#bounded-knowledge-context-and-agent-loop-phase-7a).

Pipeline: byte limit -> strict UTF-8/BOM -> conservative newline normalization ->
ATX heading/fence scanner -> normalized SHA-256 -> locked version check -> deterministic
character chunks -> provenance -> atomic PostgreSQL transaction.

Default source cap 1 MiB (trusted range 1..4 MiB); default chunks 1000 characters with
100 overlap, max_chars 64..16384, overlap 0..half max, hard ceiling 8192 chunks.
Section boundaries never overlap. Fitting fences stay whole; overlap may reduce at
fences. Oversized fences retain exact text across slices. No artificial delimiters.
Offsets reference normalized Python Unicode characters; whitespace and final newline
presence are preserved. Full Markdown/CommonMark parsing is not claimed.

CREATED introduces a logical source and revision 1. UPDATED appends a revision if
the latest normalized hash differs. UNCHANGED preserves all rows/timestamps for the
same hash, even if title/media/config changes. Intentional reprocessing requires a
future explicit rebuild contract. Different source keys remain distinct even with
equal title/content. Historical revision content and processing config are stored.

KnowledgeRepository binds tenant/namespace and provides get_by_id, get_by_source,
list_documents, acquire_source, latest_revision, get_revision, list_chunks and
append_revision. No repository commits, destructive replacement or unscoped reads.
The database must have Alembic head installed; application startup does not migrate.

Error classes in errors.py carry only fixed messages. Source/chunk bodies, titles and
keys are excluded from ingestion logs and sensitive DTO repr fields. Timing uses
perf_counter. Database exceptions are normalized outside the transaction context.
Cancellation propagates and rolls back uncommitted work; commit disconnects can have
an unknown outcome. Retrying the same source/content is idempotent, but never automatic.

See docs/decisions/0001-deterministic-knowledge-ingestion.md for adoption evidence,
Markdown limitations, schema/processing semantics and revisit criteria.
