# Tool Runtime (Phase 3)

Internal typed execution, separate from app.llm ToolCall/ToolDefinition protocol data.
Tool binds Pydantic input/output, an async handler, effect and finite timeout.
Names match `[a-z][a-z0-9_]{0,63}`. ToolRegistry explicitly registers immutable tools,
rejects duplicate names, lists deterministically and derives LLM schemas from input models.
ToolExecutor validates, enforces trusted write policy, executes once, validates output,
and emits safe ToolResult/metadata logs. Caller cancellation propagates.

build_tool_registry(database) registers only system_status, ticket_search and ticket_create.
ToolExecutionContext requires a trusted tenant UUID; inputs forbid tenant_id and policy fields.
Writes default to denied. TicketService owns transactions; repositories never commit.
Read handlers use non-committing sessions. No automatic retries or idempotency deduplication.
Timeout/commit ambiguity means a failed write must not be blindly replayed.

Phase 4 LangGraph composes this Executor. No HTTP execution route, knowledge_search/RAG, MCP, raw SQL, dynamic loading,
authentication or persistent tool tracing. See root ARCHITECTURE.md section 5 and progress.md.
