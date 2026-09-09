# Reference integration map

AgentOpsHub is the evaluation-driven enterprise Agent runtime/orchestration platform
and future harness. SupportOps and DataCopilot are separate reference applications,
not framework modules copied into this repository.

```text
User -> AgentOpsHub: platform/runtime/orchestrator/harness foundation
                   |
                   +-- SupportOps reference: Order / Refund / Ticket
                   +-- DataCopilot reference: Schema / SQL / Analysis
```

The diagram describes intended responsibilities. **Phase 6 maturity:** architecture
contracts, shared deterministic ingestion and evaluated lexical retrieval on synthetic corpora. No
cross-repository remote Agent execution, deployment or protocol interoperability
is claimed. Separate repositories retain independent deployment, ownership,
tests and versions while sharing explicit architectural contracts.

| Boundary | Intended future protocol | Phase 5 state |
| --- | --- | --- |
| AgentOpsHub -> tools/data | MCP | Recorded only |
| AgentOpsHub -> independent domain agents | A2A | Recorded only |
| AgentOpsHub -> frontend | AG-UI | Recorded only |

Each future protocol needs its own Technology Adoption Gate, authenticated context,
capability/authorization mapping, deadlines, safe DTOs, versioning and compatibility
tests. These contracts must preserve: models propose; deterministic systems validate,
authorize and execute. Repository separation exposes genuine interoperability boundaries
without importing a distributed protocol before it has demonstrated value.

Current shared interface:
trusted KnowledgeIngestionContext(tenant_id, namespace, request_id?) plus untrusted
DocumentInput(source_key, title, media_type, content: bytes) -> IngestionResult.
All knowledge queries must carry the same trusted tenant/namespace scope.


## Phase 6 shared retrieval contract

The same KnowledgeRetriever request/result contract and deterministic evaluator now
cover both namespaces. Each scenario has 8 synthetic documents and 24 fixed labeled
queries, with source-level metrics and category errors recorded in
../../evaluation/retrieval/measured-results.md.

The implementation is PostgreSQL FTS with trusted tenant/namespace and current-revision
SQL predicates. It performs no Agent invocation, knowledge_search tool dispatch or answer
generation. Shared schema, retrieval/benchmark semantics and business datasets do not
mean the independent repositories are remotely connected.
