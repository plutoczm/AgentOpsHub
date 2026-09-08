"""Validate, authorize and execute exactly once inside a bounded async boundary."""

import asyncio
import logging
from time import perf_counter

from app.llm.models import ToolCall
from app.tools.errors import (
    SAFE_MESSAGES,
    ToolError,
    ToolExecutionError,
    ToolPolicyError,
    ToolTimeoutError,
)
from app.tools.models import (
    ToolEffect,
    ToolExecutionContext,
    ToolFailure,
    ToolResult,
)
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Execute registered business tools; no retry, routing or business query logic."""

    def __init__(self, registry: ToolRegistry) -> None:
        """Use the explicitly composed registry."""
        self.registry = registry

    async def execute(self, call: ToolCall, context: ToolExecutionContext) -> ToolResult:
        """Normalize failures while allowing caller cancellation to propagate."""
        # Revalidate trusted contracts too, including mutated/model_construct instances.
        context = ToolExecutionContext.model_validate(context)
        started = perf_counter()
        metadata: dict[str, object] = {}
        if context.request_id is not None:
            metadata["request_id"] = str(context.request_id)
        try:
            tool = self.registry.lookup(call.name)
            # Never log a model-controlled unknown name, call ID, argument or tenant ID.
            metadata.update(tool_name=tool.name, tool_effect=tool.effect.value)
            logger.info("tool_start", extra=metadata)
            arguments = tool.validate_input(call.arguments)
            if tool.effect is ToolEffect.WRITE and not context.policy.allow_writes:
                raise ToolPolicyError()
            deadline = asyncio.timeout(tool.timeout_seconds)
            try:
                async with deadline:
                    value = await tool.invoke(arguments, context)
                    data = tool.validate_output(value)
            except TimeoutError:
                if deadline.expired():
                    raise ToolTimeoutError() from None
                raise ToolExecutionError() from None
            result = ToolResult(
                tool_call_id=call.id,
                tool_name=call.name,
                success=True,
                data=data,
                duration_ms=(perf_counter() - started) * 1000,
            )
        except asyncio.CancelledError:
            logger.info(
                "tool_error",
                extra={
                    **metadata,
                    "tool_outcome": "cancelled",
                    "duration_ms": (perf_counter() - started) * 1000,
                },
            )
            raise
        except Exception as exc:
            category = exc.category if isinstance(exc, ToolError) else ToolExecutionError.category
            result = ToolResult(
                tool_call_id=call.id,
                tool_name=call.name,
                success=False,
                error=ToolFailure(category=category, message=SAFE_MESSAGES[category]),
                duration_ms=(perf_counter() - started) * 1000,
            )
        logger.info(
            "tool_result" if result.success else "tool_error",
            extra={
                **metadata,
                "tool_outcome": "success" if result.success else "error",
                "tool_error": result.error.category.value if result.error else None,
                "duration_ms": result.duration_ms,
            },
        )
        return result
