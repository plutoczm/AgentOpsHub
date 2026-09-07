"""ASGI request correlation and metadata-only completion logging."""

import logging
from time import perf_counter
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.logging import request_id_context

logger = logging.getLogger(__name__)


class RequestContextMiddleware:
    """Generate server-owned IDs and preserve streaming ASGI behavior."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap an ASGI application."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Correlate HTTP responses and logs, resetting context on every exit."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_context.set(request_id)
        started = perf_counter()
        status_code = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            route = scope.get("route")
            method = scope.get("method")
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                method = "OTHER"
            logger.info(
                "request_completed",
                extra={
                    "method": method,
                    "route": getattr(route, "path", None),
                    "status_code": status_code,
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                },
            )
            request_id_context.reset(token)
