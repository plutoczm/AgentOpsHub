# Internal Agent Runtime

Phase 4: bounded LangGraph 1.2 orchestration of existing LLMGateway and ToolExecutor.
Python entry points: AgentRuntime, AgentRunRequest, AgentRunContext, AgentLimits, AgentRunResult.
No public HTTP route, checkpointer, Store or persistent memory.

Phase 7A adds a trusted optional `knowledge_namespace` to AgentRunContext and connects the
query-only `knowledge_search` tool to PostgreSQL FTS. Evidence is rank ordered, character
bounded, provenance preserving, and marked as untrusted data. Deterministic Agent integration
is evaluated with a scripted Gateway; live-model quality is not measured.

See [root README](../../../../README.md#bounded-langgraph-agent-runtime-phase-4)
for contracts, budgets and limitations, and [architecture](../../../../ARCHITECTURE.md)
for design decisions. The Phase 7A dataset and results are in
`evaluation/knowledge-agent/`.
