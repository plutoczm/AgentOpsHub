"""Small application contracts and ephemeral, replacement-update graph state."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypedDict
from uuid import UUID

from pydantic import ConfigDict, Field

from app.agents.errors import AgentConfigurationError
from app.llm.models import Contract, Identifier, Message, ToolCall
from app.tools.models import ToolExecutionContext, ToolExecutionPolicy


class AgentLimits(Contract):
    """Trusted construction-time budgets, independent of model/request payloads."""

    max_model_turns: int = Field(default=8, ge=1, le=100, strict=True)
    max_tool_calls: int = Field(default=16, ge=0, le=100, strict=True)
    total_timeout_seconds: float = Field(default=60, gt=0, le=3600, allow_inf_nan=False)

    @property
    def recursion_limit(self) -> int:
        """Allow M model + M tool nodes, a budget rejection node and END headroom."""
        return 2 * self.max_model_turns + 2


@dataclass(frozen=True, kw_only=True)
class AgentRunContext:
    """Trusted identity, knowledge scope and write policy supplied to Runtime.context."""

    tenant_id: UUID
    tool_policy: ToolExecutionPolicy = field(default_factory=ToolExecutionPolicy)
    request_id: UUID | None = None
    knowledge_namespace: str | None = None

    def __post_init__(self) -> None:
        """Validate and snapshot trusted policy using the existing tool contract."""
        try:
            validated = ToolExecutionContext(
                tenant_id=self.tenant_id,
                request_id=self.request_id,
                knowledge_namespace=self.knowledge_namespace,
                policy=self.tool_policy,
            )
        except ValueError:
            raise AgentConfigurationError() from None
        object.__setattr__(self, "tool_policy", validated.policy)
        object.__setattr__(self, "knowledge_namespace", validated.knowledge_namespace)


class AgentRunRequest(Contract):
    """Untrusted text and logical route only; no history, identity or policy fields."""

    model_config = ConfigDict(revalidate_instances="always")
    user_message: str = Field(min_length=1, repr=False)
    route: Identifier


class AgentStopReason(StrEnum):
    """Only successful completion produces a result; failures raise typed errors."""

    COMPLETED = "completed"


class AgentRunResult(Contract):
    """Safe normalized conversation and unambiguous execution counters."""

    final_message: Message = Field(repr=False)
    messages: tuple[Message, ...] = Field(repr=False)
    model_turn_count: int
    tool_calls_seen: int
    tool_executions: int
    successful_tool_count: int
    failed_tool_count: int
    duration_ms: float = Field(ge=0, allow_inf_nan=False)
    stop_reason: AgentStopReason = AgentStopReason.COMPLETED


class AgentState(TypedDict):
    """Internal single-run values; no reducers, credentials, clients or trusted identity."""

    messages: tuple[Message, ...]
    route: str
    pending_tool_calls: tuple[ToolCall, ...]
    executed_tool_call_ids: frozenset[str]
    model_turn_count: int
    tool_calls_seen: int
    tool_executions: int
    successful_tool_count: int
    failed_tool_count: int
