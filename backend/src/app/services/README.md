# Services

TicketService (Phase 3) owns per-operation sessions and uses the existing tenant-scoped
TicketRepository. Search sessions do not commit. Create uses Database.transaction();
TicketView is constructed and validated before commit. Exceptions/cancellation roll back
uncommitted changes. Ticket summaries omit description and tenant_id.

Services receive trusted application ownership explicitly. Model-generated arguments enter
through ToolExecutor validation and write policy, never through an unauthenticated HTTP route.
