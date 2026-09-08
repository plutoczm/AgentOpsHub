"""Safe orchestration errors; no framework or business payloads in messages."""


class AgentError(Exception):
    """Base failure for an incomplete run."""

    category = "runtime"
    safe_message = "Agent execution failed."

    def __init__(self) -> None:
        """Expose a fixed application-owned message only."""
        super().__init__(self.safe_message)


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
