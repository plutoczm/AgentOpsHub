"""Explicit built-in composition; no package scanning or fake placeholders."""

from app.db.session import Database
from app.knowledge.context import KnowledgeContextPolicy
from app.retrieval.postgres import PostgresFTSRetriever
from app.retrieval.protocols import KnowledgeRetriever
from app.services.tickets import TicketService
from app.tools.builtin.knowledge_search import knowledge_search_tool
from app.tools.builtin.system_status import system_status_tool
from app.tools.builtin.ticket_create import ticket_create_tool
from app.tools.builtin.ticket_search import ticket_search_tool
from app.tools.registry import ToolRegistry


def build_tool_registry(
    database: Database,
    *,
    knowledge_retriever: KnowledgeRetriever | None = None,
    knowledge_policy: KnowledgeContextPolicy | None = None,
) -> ToolRegistry:
    """Explicitly compose ticket tools and one scoped FTS knowledge tool."""
    service = TicketService(database)
    retriever = (
        knowledge_retriever if knowledge_retriever is not None else PostgresFTSRetriever(database)
    )
    registry = ToolRegistry()
    registry.register(system_status_tool(database.ready))
    registry.register(ticket_search_tool(service))
    registry.register(ticket_create_tool(service))
    registry.register(knowledge_search_tool(retriever, knowledge_policy))
    return registry
