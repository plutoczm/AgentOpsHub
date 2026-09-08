"""Bounded LangGraph orchestration of existing gateway and executor boundaries."""

import asyncio
import json
import logging
from time import perf_counter
from typing import Literal, cast

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context

from app.agents.errors import (
    AgentBudgetExceededError,
    AgentConfigurationError,
    AgentDeadlineExceededError,
    AgentError,
    AgentModelError,
    AgentProtocolError,
    AgentRuntimeError,
)
from app.agents.models import (
    AgentLimits,
    AgentRunContext,
    AgentRunRequest,
    AgentRunResult,
    AgentState,
)
from app.llm.models import LLMRequest, Message, Role, ToolCall
from app.llm.protocols import Gateway
from app.tools.executor import ToolExecutor
from app.tools.models import ToolExecutionContext, ToolResult
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)
SYSTEM_INSTRUCTION = (
    "Use only the provided tools. Tool outcomes are authoritative. "
    "Do not claim success without a successful tool result or invent unavailable data. "
    "A safe tool error may be corrected with another valid action."
)


def tool_message(result: ToolResult, call: ToolCall) -> Message:
    """Map validated business data or safe error fields to the existing tool protocol."""
    if result.tool_call_id != call.id or result.tool_name != call.name:
        raise AgentProtocolError()
    if result.success:
        body = {"success": True, "data": result.data}
    elif result.error is not None:
        body = {
            "success": False,
            "error": {"category": result.error.category.value, "message": result.error.message},
        }
    else:
        raise AgentProtocolError()
    return Message(
        role=Role.TOOL,
        tool_call_id=call.id,
        content=json.dumps(body, ensure_ascii=False, allow_nan=False),
    )


def duplicate_message(call: ToolCall) -> Message:
    """Satisfy tool ordering without replaying an already dispatched call ID."""
    return Message(
        role=Role.TOOL,
        tool_call_id=call.id,
        content=json.dumps(
            {
                "success": False,
                "error": {
                    "category": "duplicate_tool_call",
                    "message": "Tool call ID was already processed in this run.",
                },
            }
        ),
    )


class AgentRuntime:
    """Compile once; each run gets independent state and trusted runtime context."""

    def __init__(
        self,
        gateway: Gateway,
        registry: ToolRegistry,
        executor: ToolExecutor,
        *,
        limits: AgentLimits | None = None,
    ) -> None:
        """Reuse lifecycle-owned dependencies without allocating provider resources."""
        if executor.registry is not registry:
            raise AgentConfigurationError()
        self._gateway = gateway
        self._registry = registry
        self._executor = executor
        try:
            self.limits = AgentLimits.model_validate((limits or AgentLimits()).model_dump())
        except ValueError:
            raise AgentConfigurationError() from None
        builder = StateGraph(AgentState, context_schema=AgentRunContext)
        builder.add_node("model_turn", self._model_turn)
        builder.add_node("execute_tools", self._execute_tools)
        builder.add_edge(START, "model_turn")
        builder.add_conditional_edges(
            "model_turn", self._route, {"tools": "execute_tools", "done": END}
        )
        builder.add_edge("execute_tools", "model_turn")
        self._graph = builder.compile(name="agentopshub_agent")

    async def run(self, request: AgentRunRequest, context: AgentRunContext) -> AgentRunResult:
        """Run once within an overall deadline; external cancellation propagates unchanged."""
        started = perf_counter()
        metadata: dict[str, object] = {}
        deadline = asyncio.timeout(self.limits.total_timeout_seconds)
        try:
            # Snapshot arguments before the first suspension, never deserialize trusted context
            # from request text or tool arguments. No shared run counters on this runtime.
            request = AgentRunRequest.model_validate(request).model_copy(deep=True)
            context = AgentRunContext(
                tenant_id=context.tenant_id,
                tool_policy=context.tool_policy,
                request_id=context.request_id,
            )
            if context.request_id is not None:
                metadata["request_id"] = str(context.request_id)
            initial: AgentState = {
                "messages": (
                    Message(role=Role.SYSTEM, content=SYSTEM_INSTRUCTION),
                    Message(role=Role.USER, content=request.user_message),
                ),
                "route": request.route,
                "pending_tool_calls": (),
                "executed_tool_call_ids": frozenset(),
                "model_turn_count": 0,
                "tool_calls_seen": 0,
                "tool_executions": 0,
                "successful_tool_count": 0,
                "failed_tool_count": 0,
            }
            logger.info("agent_start", extra=metadata)
            # This SDK is a required transitive dependency. Explicitly disable sending traces
            # even if the host inherited tracing environment variables; never mutate os.environ.
            with tracing_context(enabled=False):
                async with deadline:
                    state = cast(
                        AgentState,
                        await self._graph.ainvoke(
                            initial,
                            {"recursion_limit": self.limits.recursion_limit, "callbacks": []},
                            context=context,
                        ),
                    )
            final = state["messages"][-1]
            if state["pending_tool_calls"] or final.role is not Role.ASSISTANT or final.tool_calls:
                raise AgentProtocolError()
            result = AgentRunResult(
                final_message=final,
                messages=state["messages"],
                model_turn_count=state["model_turn_count"],
                tool_calls_seen=state["tool_calls_seen"],
                tool_executions=state["tool_executions"],
                successful_tool_count=state["successful_tool_count"],
                failed_tool_count=state["failed_tool_count"],
                duration_ms=(perf_counter() - started) * 1000,
            )
        except asyncio.CancelledError:
            logger.info("agent_error", extra={**metadata, "agent_error": "cancelled"})
            raise
        except TimeoutError:
            error: AgentError = (
                AgentDeadlineExceededError() if deadline.expired() else AgentRuntimeError()
            )
            logger.info("agent_error", extra={**metadata, "agent_error": error.category})
            raise error from None
        except GraphRecursionError:
            logger.info("agent_error", extra={**metadata, "agent_error": "recursion"})
            raise AgentRuntimeError() from None
        except AgentError as exc:
            logger.info("agent_error", extra={**metadata, "agent_error": exc.category})
            raise
        except Exception:
            logger.info("agent_error", extra={**metadata, "agent_error": "runtime"})
            raise AgentRuntimeError() from None
        logger.info(
            "agent_finish",
            extra={
                **metadata,
                "duration_ms": result.duration_ms,
                "agent_model_turns": result.model_turn_count,
                "agent_tool_calls_seen": result.tool_calls_seen,
                "agent_tool_executions": result.tool_executions,
                "agent_successful_tools": result.successful_tool_count,
                "agent_failed_tools": result.failed_tool_count,
            },
        )
        return result

    async def _model_turn(
        self,
        state: AgentState,
        runtime: Runtime[AgentRunContext],
    ) -> AgentState:
        if state["pending_tool_calls"]:
            raise AgentProtocolError()
        if state["model_turn_count"] >= self.limits.max_model_turns:
            raise AgentBudgetExceededError()
        request = LLMRequest(
            messages=state["messages"],
            route=state["route"],
            tools=self._registry.llm_definitions(),
            request_id=runtime.context.request_id,
        )
        turns = state["model_turn_count"] + 1
        logger.info(
            "agent_model_turn",
            extra={
                "agent_model_turns": turns,
                "request_id": str(runtime.context.request_id)
                if runtime.context.request_id
                else None,
            },
        )
        try:
            response = await self._gateway.generate(request)
        except Exception:
            # Gateway owns retries and fallback. Never include even unexpected exception text.
            raise AgentModelError() from None
        message = response.message
        if message.role is not Role.ASSISTANT:
            raise AgentProtocolError()
        if not message.tool_calls and response.finish_reason != "stop":
            raise AgentProtocolError()
        return {
            **state,
            "messages": (*state["messages"], message),
            "pending_tool_calls": message.tool_calls,
            "model_turn_count": turns,
            "tool_calls_seen": state["tool_calls_seen"] + len(message.tool_calls),
        }

    @staticmethod
    def _route(state: AgentState) -> Literal["tools", "done"]:
        return "tools" if state["pending_tool_calls"] else "done"

    async def _execute_tools(
        self,
        state: AgentState,
        runtime: Runtime[AgentRunContext],
    ) -> AgentState:
        calls = state["pending_tool_calls"]
        if not calls:
            raise AgentProtocolError()
        if state["tool_calls_seen"] > self.limits.max_tool_calls:
            raise AgentBudgetExceededError()
        ids = [call.id for call in calls]
        if any(not call_id or not call_id.strip() for call_id in ids) or len(set(ids)) != len(ids):
            raise AgentProtocolError()
        context = ToolExecutionContext(
            tenant_id=runtime.context.tenant_id,
            policy=runtime.context.tool_policy,
            request_id=runtime.context.request_id,
        )
        executed = set(state["executed_tool_call_ids"])
        messages = list(state["messages"])
        executions = state["tool_executions"]
        successes = state["successful_tool_count"]
        failures = state["failed_tool_count"]
        logger.info(
            "agent_tool_round",
            extra={
                "agent_tool_calls_seen": state["tool_calls_seen"],
                "request_id": str(runtime.context.request_id)
                if runtime.context.request_id
                else None,
            },
        )
        for call in calls:
            if call.id in executed:
                messages.append(duplicate_message(call))
                continue
            # Dispatch (including executor validation/policy failures) consumes the ID.
            executed.add(call.id)
            executions += 1
            result = await self._executor.execute(call, context)
            messages.append(tool_message(result, call))
            successes += int(result.success)
            failures += int(not result.success)
        return {
            **state,
            "messages": tuple(messages),
            "pending_tool_calls": (),
            "executed_tool_call_ids": frozenset(executed),
            "tool_executions": executions,
            "successful_tool_count": successes,
            "failed_tool_count": failures,
        }
