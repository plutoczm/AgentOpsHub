from collections.abc import Awaitable, Callable, Sequence
from uuid import uuid4

from app.agents import AgentLimits, AgentRunContext, AgentRunRequest, AgentRuntime
from app.llm.models import DeploymentType, LLMRequest, LLMResponse, Message, Role, ToolCall
from app.tools import ToolExecutor, ToolRegistry

type Step = Message | LLMResponse | Exception | Callable[[LLMRequest], Awaitable[Message]]


def answer(text: str = "Final answer") -> Message:
    return Message(role=Role.ASSISTANT, content=text)


def calls(*items: ToolCall) -> Message:
    return Message(role=Role.ASSISTANT, tool_calls=items)


def call(call_id: str = "call-1", name: str = "probe", **arguments: str | int | bool) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=dict(arguments))


def response(message: Message) -> LLMResponse:
    return LLMResponse(
        message=message,
        finish_reason="tool_calls" if message.tool_calls else "stop",
        provider="script",
        model="offline",
        deployment_type=DeploymentType.PRIVATE,
        latency_ms=0,
        attempts=(),
    )


class ScriptedGateway:
    def __init__(self, steps: Sequence[Step]) -> None:
        self.steps = list(steps)
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request.model_copy(deep=True))
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            step = await step(request)
        return response(step) if isinstance(step, Message) else step


def runtime(
    gateway: ScriptedGateway,
    registry: ToolRegistry | None = None,
    limits: AgentLimits | None = None,
) -> AgentRuntime:
    registry = registry or ToolRegistry()
    return AgentRuntime(gateway, registry, ToolExecutor(registry), limits=limits)


def request() -> AgentRunRequest:
    return AgentRunRequest(user_message="PRIVATE USER PROMPT", route="test-route")


def context() -> AgentRunContext:
    return AgentRunContext(tenant_id=uuid4(), request_id=uuid4())
