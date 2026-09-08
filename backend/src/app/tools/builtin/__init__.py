"""Explicit built-in composition; no package scanning or fake placeholders."""

from app.db.session import Database
from app.services.tickets import TicketService
from app.tools.builtin.system_status import system_status_tool
from app.tools.builtin.ticket_create import ticket_create_tool
from app.tools.builtin.ticket_search import ticket_search_tool
from app.tools.registry import ToolRegistry


def build_tool_registry(database: Database) -> ToolRegistry:
    """Register exactly the three implemented tools using application-owned services."""
    service = TicketService(database)
    registry = ToolRegistry()
    registry.register(system_status_tool(database.ready))
    registry.register(ticket_search_tool(service))
    registry.register(ticket_create_tool(service))
    return registry
