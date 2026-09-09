# SupportOps reference scenario

Role: independently deployable customer-support application/domain agent for Order,
Refund and Ticket. AgentOpsHub supplies common runtime/orchestration/harness and the
generic knowledge ingestion substrate. Phase 5 defines this contract and synthetic
supportops knowledge; there is no remote runtime connection or copied domain source.

| Classification | Intended responsibility |
| --- | --- |
| Deterministic workflow | Identity/tenant checks, order retrieval, refund eligibility, business policy, write authorization, idempotency and transaction |
| Agentic decisions | Ambiguous intent, troubleshooting and choosing an appropriate support capability |
| Hybrid resolution | Model proposes a refund/support action; deterministic policy validates and authorizes; workflow executes |

Trusted application code selects tenant_id and namespace=supportops, then calls
KnowledgeIngestionService.ingest(DocumentInput, KnowledgeIngestionContext).
The document body cannot choose ownership or persistence fields. Source keys are
logical identifiers, not local filenames to open or URLs to fetch.

Synthetic fixtures: refund_policy.md, ticket_escalation.md, order_support.md under
examples/knowledge/supportops. They are fictional examples, not a merchant's policy.
The same parser/chunker/repository serves DataCopilot.

Future integration must specify authenticated tenant mapping, allowed capabilities,
write authorization and idempotency, deadline/cancellation, safe result/error DTOs,
version compatibility and independent regression tests. SupportOps owns domain policy;
AgentOpsHub does not infer permission from a model or document.

Repositories remain separate for independent deployment, domain ownership, tests and
versions. Future A2A is a candidate boundary requiring a separate adoption decision;
Phase 5 does not install or implement it. See integration-map.md.


## Phase 6 shared retrieval contract

The same KnowledgeRetriever request/result contract and deterministic evaluator now
cover both namespaces. Each scenario has 8 synthetic documents and 24 fixed labeled
queries, with source-level metrics and category errors recorded in
../../evaluation/retrieval/measured-results.md.

The implementation is PostgreSQL FTS with trusted tenant/namespace and current-revision
SQL predicates. It performs no Agent invocation, knowledge_search tool dispatch or answer
generation. Shared schema, retrieval/benchmark semantics and business datasets do not
mean the independent repositories are remotely connected.
