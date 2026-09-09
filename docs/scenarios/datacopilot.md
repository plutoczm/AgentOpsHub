# DataCopilot reference scenario

Role: independently deployable data-engineering application/domain agent for Schema,
SQL and Analysis. AgentOpsHub provides platform runtime/orchestration/harness and a
generic ingestion substrate. Phase 5 defines a shared contract and synthetic
datacopilot knowledge. Text2SQL and remote integration are not implemented here.

| Classification | Intended responsibility |
| --- | --- |
| Deterministic workflow | Schema inspection/constraints, SQL AST validation, read/write safety, query timeout/limit, EXPLAIN/dry-run and execution verification |
| Agentic decisions | Semantic intent understanding, schema reasoning and analytical synthesis |
| Hybrid Text2SQL | Model proposes SQL; deterministic validator checks; controlled executor executes; verifier checks results |

Trusted application code chooses tenant_id and namespace=datacopilot. DocumentInput
contains only logical source key, title, explicit media type and bytes. SQL in the
document is inert content; ingestion neither evaluates SQL nor contacts a warehouse.

Synthetic fixtures: sql_standards.md, warehouse_conventions.md and schema_guidelines.md
under examples/knowledge/datacopilot. No production schema, customer data or credentials.
Generic tests prove the same pipeline handles this corpus and SupportOps.

A future adapter must define authenticated tenant mapping, schema/data permissions,
read/write capability contracts, bounded query execution, safe error/result DTOs,
provenance, version compatibility and independently owned tests. DataCopilot retains
domain execution policy and deployment; no repository merge or Git submodule is needed.

A2A integration requires a later adoption gate. No protocol package or domain-agent
implementation is added in Phase 5. See integration-map.md.


## Phase 6 shared retrieval contract

The same KnowledgeRetriever request/result contract and deterministic evaluator now
cover both namespaces. Each scenario has 8 synthetic documents and 24 fixed labeled
queries, with source-level metrics and category errors recorded in
../../evaluation/retrieval/measured-results.md.

The implementation is PostgreSQL FTS with trusted tenant/namespace and current-revision
SQL predicates. It performs no Agent invocation, knowledge_search tool dispatch or answer
generation. Shared schema, retrieval/benchmark semantics and business datasets do not
mean the independent repositories are remotely connected.
