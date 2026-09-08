"""Provider-independent, text-only LLM contracts."""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_./:-]+$")]
ToolName = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
JsonObject = dict[str, JsonValue]
TokenCount = Annotated[int, Field(ge=0, strict=True)]


class Contract(BaseModel):
    """Reject unknown application fields and avoid input values in validation errors."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class Role(StrEnum):
    """Roles understood by the normalized chat protocol."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class DeploymentType(StrEnum):
    """Descriptive metadata only; routing does not branch on deployment class."""

    CLOUD = "cloud"
    LOCAL = "local"
    PRIVATE = "private"


class ErrorCategory(StrEnum):
    """Semantic errors independent of HTTP client or provider implementation."""

    CONFIGURATION = "configuration"
    AUTHENTICATION = "authentication"
    BAD_REQUEST = "bad_request"
    RATE_LIMIT = "rate_limit"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    RESPONSE = "response"
    UNSUPPORTED = "unsupported_capability"
    STRUCTURED = "structured_output"
    EXHAUSTED = "exhausted"


class Capabilities(Contract):
    """Declared features; compatibility alone does not imply support."""

    tool_calling: bool = False
    json_mode: bool = False
    structured_output: bool = False


class ToolDefinition(Contract):
    """A function declaration, never an executable implementation."""

    name: ToolName
    description: str = Field(default="", max_length=10000, repr=False)
    parameters: JsonObject = Field(repr=False)

    @model_validator(mode="after")
    def object_schema(self) -> Self:
        """Require an object schema for function arguments."""
        if self.parameters.get("type") != "object":
            raise ValueError("Tool parameters must declare an object schema")
        return self


class ToolCall(Contract):
    """Parsed function call data; arguments remain untrusted input."""

    id: Identifier
    name: ToolName
    arguments: JsonObject = Field(repr=False)


class Message(Contract):
    """Text and tool-protocol messages, with payloads hidden from repr."""

    role: Role
    content: str | None = Field(default=None, repr=False)
    tool_calls: tuple[ToolCall, ...] = Field(default=(), repr=False)
    tool_call_id: Identifier | None = None

    @model_validator(mode="after")
    def role_contract(self) -> Self:
        """Reject tool fields on wrong roles and contentless ordinary messages."""
        if self.role is Role.TOOL:
            if self.tool_call_id is None or self.content is None:
                raise ValueError("Tool messages require content and tool_call_id")
        elif self.tool_call_id is not None:
            raise ValueError("tool_call_id is only valid on tool messages")
        if self.tool_calls and self.role is not Role.ASSISTANT:
            raise ValueError("Only assistant messages may contain tool calls")
        if self.content is None and not self.tool_calls:
            raise ValueError("A message requires text or assistant tool calls")
        return self


class ModelTarget(Contract):
    """A profile reference plus optional model-specific capability overrides."""

    provider: Identifier
    model: Identifier | None = None
    capabilities: Capabilities | None = None


class LLMRequest(Contract):
    """Internal chat request, selecting either a logical route or an explicit target."""

    messages: tuple[Message, ...] = Field(min_length=1, max_length=500, repr=False)
    route: Identifier | None = None
    target: ModelTarget | None = None
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    max_output_tokens: int = Field(default=512, ge=1, le=1000000, strict=True)
    stop: tuple[Annotated[str, Field(min_length=1)], ...] = Field(
        default=(), max_length=4, repr=False
    )
    json_mode: bool = False
    structured_schema: JsonObject | None = Field(default=None, repr=False)
    tools: tuple[ToolDefinition, ...] = Field(default=(), max_length=128, repr=False)
    request_id: UUID | None = None

    @model_validator(mode="after")
    def selector_and_tools(self) -> Self:
        """Require an unambiguous target and unique tool declarations."""
        if (self.route is None) == (self.target is None):
            raise ValueError("Specify exactly one route or explicit target")
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("Tool names must be unique")
        if self.structured_schema is not None and self.structured_schema.get("type") != "object":
            raise ValueError("Structured output requires an object schema")
        return self


class Usage(Contract):
    """Unknown token counts remain None; only a missing complete total is derived."""

    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None
    total_tokens: TokenCount | None = None

    @model_validator(mode="after")
    def derive_total(self) -> Self:
        """Derive total only when both component counts were supplied."""
        if (
            self.total_tokens is None
            and self.input_tokens is not None
            and self.output_tokens is not None
        ):
            object.__setattr__(self, "total_tokens", self.input_tokens + self.output_tokens)
        return self


class CostEstimate(Contract):
    """Configured price estimate for one successful response, not an invoice."""

    input_cost: Decimal
    output_cost: Decimal
    total_cost: Decimal
    currency: str


class Attempt(Contract):
    """Safe in-memory metadata for an actual transport attempt."""

    sequence: int
    provider: Identifier
    model: Identifier
    deployment_type: DeploymentType
    attempt_number: int
    outcome: Literal["success", "error", "cancelled"]
    error_category: ErrorCategory | None = None
    http_status: int | None = None
    latency_ms: float = Field(ge=0)
    retryable: bool = False


class ProviderResult(Contract):
    """A transport-normalized result before gateway routing metadata is attached."""

    message: Message = Field(repr=False)
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter", "other"]
    usage: Usage = Field(default_factory=Usage)
    provider_request_id: str | None = None


class LLMResponse(ProviderResult):
    """Final response without vendor JSON, prompts or raw transport exceptions."""

    provider: Identifier
    model: Identifier
    deployment_type: DeploymentType
    latency_ms: float = Field(ge=0)
    attempts: tuple[Attempt, ...]
    request_id: UUID | None = None
    cost: CostEstimate | None = None


class StructuredResult[T: BaseModel](Contract):
    """Validated application data plus normalized response/accounting metadata."""

    value: T = Field(repr=False)
    response: LLMResponse = Field(repr=False)
