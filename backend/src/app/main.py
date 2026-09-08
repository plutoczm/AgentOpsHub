"""FastAPI composition root with lifecycle-owned internal runtimes."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.agents import AgentRuntime
from app.api.health import router
from app.api.readiness import router as readiness_router
from app.core.config import Settings, load_settings
from app.core.middleware import RequestContextMiddleware
from app.db.session import Database
from app.llm.gateway import LLMGateway
from app.observability.logging import configure_logging
from app.tools.builtin import build_tool_registry
from app.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)


async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Return a generic error and log type metadata without exception contents."""
    request_id: str = request.state.request_id
    logger.error(
        "unhandled_exception",
        extra={"request_id": request_id, "error_type": type(exc).__name__},
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id},
        headers={"X-Request-ID": request_id},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construct an isolated app; importing this module performs no I/O."""
    resolved = settings if settings is not None else load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved.log_level)
        database = Database(resolved)
        app.state.database = database
        gateway = LLMGateway(resolved.llm)
        app.state.llm_gateway = gateway
        app.state.tool_registry = build_tool_registry(database)
        app.state.tool_executor = ToolExecutor(app.state.tool_registry)
        app.state.agent_runtime = AgentRuntime(
            gateway, app.state.tool_registry, app.state.tool_executor
        )
        logger.info("application_started")
        try:
            yield
        finally:
            try:
                await gateway.close()
            finally:
                await database.close()
            logger.info("application_stopped")

    docs_enabled = resolved.environment != "production"
    app = FastAPI(
        title=resolved.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.settings = resolved
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(Exception, unhandled_exception)
    app.include_router(router)
    app.include_router(readiness_router)
    return app
