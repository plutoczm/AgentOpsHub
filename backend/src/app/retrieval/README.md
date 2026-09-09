# Internal PostgreSQL FTS lexical baseline

PostgresFTSRetriever implements KnowledgeRetriever.search(context, request).
It is neither BM25 nor a RAG answer generator.

```python
context = KnowledgeRetrievalContext(tenant_id=trusted_tenant, namespace="supportops")
request = KnowledgeSearchRequest(query="unopened 14", top_k=5)
result = await PostgresFTSRetriever(database).search(context, request)
```

Only trusted context chooses tenant/namespace. Request forbids ownership, table,
ranking expression and revision controls. Raw query <=512 characters before trim;
nonblank after trim; top_k=5 by default, 1..20. Nothing executes query text as SQL.

One bound SELECT searches current/latest revisions only. It uses the explicit
pg_catalog.simple configuration, plainto_tsquery AND matching, and ts_rank_cd(...,0).
Order is score DESC, source_key with C collation, chunk index, then UUID.
Score is a backend-specific finite number, not probability/confidence. Result models
permit future negative dense similarities without changing the contract.

Returned chunks contain document/revision/chunk UUIDs, source key, revision title,
namespace, chunk index, section path, content/hash, normalized character range,
score and contiguous one-based rank. Result carries retriever identity and monotonic
duration. Content is internal application data and never emitted in retrieval logs.

GIN expression index is introduced by 20260909_02; earlier migrations are unchanged.
No current-pointer column or ingestion semantic change. The SQL excludes every revision
that has a higher-numbered sibling, within the statement snapshot. The index itself
retains historical entries; no stale retrieval or Python authorization filtering.

Read sessions do not commit. Fixed errors distinguish request/context, backend and
output invariant failures. Raw PostgreSQL errors and source/query bodies are suppressed.
Existing pool/driver limits bound database I/O; no new global retrieval deadline is claimed.

simple has no stemming, synonym/acronym expansion or strong Chinese segmentation.
AND terms must co-occur in one chunk; title/ancestry is not prepended to every chunk.
Phase 5 metadata/config-only UNCHANGED semantics remain; explicit reindex/rebuild and
metadata-version operations are future work.

See evaluation/retrieval/README.md for the frozen synthetic benchmark, metrics and
actual limitations. No public endpoint, knowledge_search tool, Agent/LLM call,
embedding, Qdrant business retrieval, Dense, Hybrid/RRF or reranker is implemented.
