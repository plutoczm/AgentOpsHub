# Tool Runtime (Phase 3)

Internal typed execution, separate from app.llm ToolCall/ToolDefinition protocol data.
Tool binds Pydantic input/output, an async handler, effect and finite timeout.
Names match `[a-z][a-z0-9_]{0,63}`. ToolRegistry explicitly registers immutable tools,
rejects duplicate names, lists deterministically and derives LLM schemas from input models.
ToolExecutor validates, enforces trusted write policy, executes once, validates output,
and emits safe ToolResult/metadata logs. Caller cancellation propagates.

build_tool_registry(database) explicitly registers system_status, ticket_search,
ticket_create, and the Phase 7A `knowledge_search` tool. The latter accepts only `query`;
tenant, namespace, retriever and evidence limits come from trusted application context/policy.
ToolExecutionContext requires a trusted tenant UUID and accepts an optional typed
knowledge_namespace supplied by the Agent runtime; inputs forbid tenant_id and policy fields.
Writes default to denied. TicketService owns transactions; repositories never commit.
Read handlers use non-committing sessions. No automatic retries or idempotency deduplication.
Timeout/commit ambiguity means a failed write must not be blindly replayed.

Phase 4 LangGraph composes this Executor, and Phase 7A uses the same executor for the
internal knowledge loop. No HTTP execution route, MCP, raw SQL, dynamic loading,
authentication or persistent tool tracing. See root ARCHITECTURE.md section 5 and 6.
