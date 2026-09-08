"""Conservative structured logging with fixed events and approved metadata."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
EVENTS = frozenset(
    {
        "application_started",
        "application_stopped",
        "database_unavailable",
        "transaction_failed",
        "request_completed",
        "unhandled_exception",
    }
)
FIELDS = ("method", "route", "status_code", "duration_ms", "error_type")


class JsonFormatter(logging.Formatter):
    """Emit JSON without arbitrary messages, exception text or request payloads.

    Unknown log messages become the generic event 'log'. This deliberately
    trades third-party message detail for a safe bootstrap logging baseline.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Serialize only the event allowlist and approved structured fields."""
        event = record.msg if isinstance(record.msg, str) and record.msg in EVENTS else "log"
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": event,
            "request_id": getattr(record, "request_id", request_id_context.get()),
        }
        for field in FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, ensure_ascii=False, allow_nan=False)


def configure_logging(level: str) -> None:
    """Configure one stdout handler, including Uvicorn's application logs."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
