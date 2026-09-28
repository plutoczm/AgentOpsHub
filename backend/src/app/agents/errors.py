"""Safe orchestration errors; no framework or business payloads in messages."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agents.tracing import AgentTraceEvent, AgentTraceSummary


class AgentError(Exception):
    """Base failure for an incomplete run."""

    category = "runtime"
    safe_message = "Agent execution failed."

    def __init__(self) -> None:
        """Expose a fixed application-owned message only."""
        super().__init__(self.safe_message)
        self.trace_events: tuple[AgentTraceEvent, ...] = ()
        self.trace_summary: AgentTraceSummary | None = None

    def attach_trace(
        self,
        events: tuple[AgentTraceEvent, ...],
        summary: AgentTraceSummary,
    ) -> None:
        """Attach only a typed safe trace owned by this failed run."""
        self.trace_events = events
        self.trace_summary = summary


class AgentConfigurationError(AgentError):
    """Invalid trusted runtime configuration."""

    category = "configuration"
    safe_message = "Agent configuration is invalid."


class AgentBudgetExceededError(AgentError):
    """An application semantic budget was exhausted."""

    category = "budget"
    safe_message = "Agent execution budget exceeded."


class AgentDeadlineExceededError(AgentError):
    """The single overall run deadline expired."""

    category = "deadline"
    safe_message = "Agent execution deadline exceeded."


class AgentModelError(AgentError):
    """The gateway failed; the agent does not retry it."""

    category = "model"
    safe_message = "Agent model invocation failed."


class AgentProtocolError(AgentError):
    """The assistant/tool sequence violates an orchestration invariant."""

    category = "protocol"
    safe_message = "Agent conversation protocol is invalid."


class AgentRuntimeError(AgentError):
    """An unexpected graph failure, including the recursion circuit breaker."""

    category = "runtime"
