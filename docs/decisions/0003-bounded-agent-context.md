# Decision 0003: bounded knowledge context before retrieval expansion

Status: accepted for Phase 7A.

## Decision

Connect the accepted PostgreSQL FTS retriever to the existing typed tool registry and
bounded `AgentRuntime`. Assemble a deterministic, provenance-preserving evidence pack
under trusted character limits. Keep the retrieval backend, FTS query, ranking, and frozen
Phase 6 benchmark unchanged.

The trusted application supplies tenant, namespace, and construction-time evidence policy.
The model supplies only the `knowledge_search` query. Retrieved document text is marked and
handled as untrusted evidence. Empty retrieval is a normal result; backend and assembly
failures remain safe tool errors.

## Why this stage comes first

The frozen 16-document/48-query Phase 6 benchmark measured paraphrase HitRate@5 at `0/8`
and ordinary wording at `4/8`. Those misses make Dense retrieval worth a controlled
comparison, but they do not show that Dense will improve an Agent's task outcome. First,
the system needs a reproducible path that connects retrieval changes to bounded context,
provenance, and deterministic Agent task results.

Phase 7A supplies that integration path without changing retrieval quality. The next
authorized evaluation stage can measure live-model behavior and minimal tracing while
keeping scripted integration correctness separate from model quality.

## Alternatives deferred

- Dense embeddings and Qdrant: no controlled comparison or Agent-task attribution path existed yet.
- Hybrid/RRF and reranking: no measured complementary failure or gain justified their extra configuration.
- MCP, A2A, public Agent API, and UI: outside this local internal-runtime integration boundary.

## Verification and limits

The versioned Phase 7A dataset passed `10/10` deterministic cases twice on separate
disposable PostgreSQL databases; stable case outcomes and context fingerprints matched.
The Phase 6 combined HitRate@5/Recall@5 remained `0.642857143`/`0.619047619`. The full
PostgreSQL integration suite passed `535` tests. The deterministic Gateway verifies
runtime/tool/retriever/context integration only; real-model quality and prompt-injection
behavior remain unmeasured.

See [Phase 7A measured results](../../evaluation/knowledge-agent/measured-results.md) and
[Phase 6 measured results](../../evaluation/retrieval/measured-results.md).
