import asyncio
import logging
from dataclasses import replace
from hashlib import sha256
from uuid import uuid4

import pytest

from app.llm.models import JsonObject, ToolCall
from app.retrieval.errors import RetrievalBackendError
from app.retrieval.models import (
    KnowledgeRetrievalContext,
    KnowledgeSearchRequest,
    RetrievalResult,
    RetrievedChunk,
)
from app.tools import ToolEffect, ToolExecutionContext, ToolExecutor, ToolRegistry
from app.tools.builtin.knowledge_search import (
    KnowledgeSearchInput,
    KnowledgeSearchOutput,
    knowledge_search_tool,
)
from app.tools.contracts import Tool
from app.tools.models import ToolErrorCategory


class FakeRetriever:
    name = "test-fts-v1"

    def __init__(self, *, chunks: tuple[RetrievedChunk, ...] = (), error: Exception | None = None):
        self.chunks = chunks
        self.error = error
        self.context: KnowledgeRetrievalContext | None = None
        self.request: KnowledgeSearchRequest | None = None

    async def search(
        self,
        context: KnowledgeRetrievalContext,
        request: KnowledgeSearchRequest,
    ) -> RetrievalResult:
        self.context = context
        self.request = request
        if self.error is not None:
            raise self.error
        return RetrievalResult(
            retriever=self.name,
            namespace=context.namespace,
            chunks=self.chunks,
            duration_ms=2.5,
        )


def retrieved(content: str, *, source_key: str = "safe-source") -> RetrievedChunk:
    return RetrievedChunk(
        document_id=uuid4(),
        revision_id=uuid4(),
        chunk_id=uuid4(),
        source_key=source_key,
        title="Private title marker",
        namespace="supportops",
        chunk_index=0,
        section_path=("Returns",),
        content=content,
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        character_start=0,
        character_end=len(content),
        score=1.0,
        rank=1,
    )


def executor_for(
    retriever: FakeRetriever,
) -> tuple[ToolExecutor, Tool[KnowledgeSearchInput, KnowledgeSearchOutput]]:
    tool = knowledge_search_tool(retriever)
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(registry), tool


def call(arguments: JsonObject) -> ToolCall:
    return ToolCall.model_validate(
        {"id": "knowledge-1", "name": "knowledge_search", "arguments": arguments}
    )


def context(*, namespace: str | None = "supportops") -> ToolExecutionContext:
    return ToolExecutionContext(
        tenant_id=uuid4(),
        request_id=uuid4(),
        knowledge_namespace=namespace,
    )


@pytest.mark.anyio
async def test_tool_is_read_only_exposes_only_query_and_uses_trusted_scope() -> None:
    retriever = FakeRetriever(chunks=(retrieved("Current return window is 30 days."),))
    executor, tool = executor_for(retriever)
    trusted = context()

    outcome = await executor.execute(call({"query": "return window"}), trusted)

    schema = tool.llm_definition().parameters
    properties = schema.get("properties")
    assert tool.effect is ToolEffect.READ_ONLY
    assert schema["required"] == ["query"]
    assert isinstance(properties, dict) and set(properties) == {"query"}
    assert schema["additionalProperties"] is False
    assert retriever.context is not None
    assert retriever.context.tenant_id == trusted.tenant_id
    assert retriever.context.namespace == trusted.knowledge_namespace == "supportops"
    assert retriever.context.request_id == trusted.request_id
    assert retriever.request is not None and retriever.request.top_k == 10
    assert outcome.success and outcome.data is not None
    assert outcome.data["status"] == "evidence"
    assert outcome.data["evidence_trust"] == "untrusted_evidence"
    assert outcome.data["evidence_count"] == outcome.data["retrieved_count"] == 1
    assert "score" not in str(outcome.data) and "confidence" not in str(outcome.data)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "extra",
    ["tenant_id", "namespace", "revision", "source_key", "top_k", "backend"],
)
async def test_tool_rejects_all_scope_backend_revision_and_budget_overrides(extra: str) -> None:
    retriever = FakeRetriever()
    executor, _ = executor_for(retriever)
    arguments: JsonObject = {"query": "return policy", extra: "override"}

    outcome = await executor.execute(call(arguments), context())

    assert outcome.error is not None
    assert outcome.error.category is ToolErrorCategory.INPUT_VALIDATION
    assert retriever.request is None


@pytest.mark.anyio
@pytest.mark.parametrize("query", ["", "   ", "x" * 513, "bad\x00query"])
async def test_tool_rejects_invalid_query_text_before_retrieval(query: str) -> None:
    retriever = FakeRetriever()
    executor, _ = executor_for(retriever)

    outcome = await executor.execute(call({"query": query}), context())

    assert outcome.error is not None
    assert outcome.error.category is ToolErrorCategory.INPUT_VALIDATION
    assert retriever.request is None


@pytest.mark.anyio
async def test_no_evidence_is_a_valid_success_and_missing_namespace_fails_closed() -> None:
    retriever = FakeRetriever()
    executor, _ = executor_for(retriever)

    no_evidence = await executor.execute(call({"query": "absent terms"}), context())
    missing_scope = await executor.execute(
        call({"query": "should not dispatch"}), context(namespace=None)
    )

    assert no_evidence.success and no_evidence.data is not None
    assert no_evidence.data["status"] == "no_evidence"
    assert no_evidence.data["evidence"] == []
    assert missing_scope.error is not None
    assert missing_scope.error.category is ToolErrorCategory.EXECUTION
    assert retriever.request is not None and retriever.request.query == "absent terms"


@pytest.mark.anyio
async def test_backend_failure_is_distinct_from_no_evidence_and_safe() -> None:
    retriever = FakeRetriever(error=RetrievalBackendError())
    executor, _ = executor_for(retriever)

    outcome = await executor.execute(call({"query": "PRIVATE QUERY MARKER"}), context())

    assert not outcome.success and outcome.error is not None
    assert outcome.error.category is ToolErrorCategory.EXECUTION
    assert outcome.data is None
    assert "PRIVATE SQL" not in outcome.model_dump_json()
    assert "PRIVATE QUERY MARKER" not in outcome.model_dump_json()


@pytest.mark.anyio
async def test_existing_executor_timeout_semantics_apply_to_knowledge_tool() -> None:
    class SlowRetriever(FakeRetriever):
        async def search(
            self,
            context: KnowledgeRetrievalContext,
            request: KnowledgeSearchRequest,
        ) -> RetrievalResult:
            await asyncio.sleep(0.05)
            return await super().search(context, request)

    slow = knowledge_search_tool(SlowRetriever())
    short = replace(slow, timeout_seconds=0.005)
    registry = ToolRegistry()
    registry.register(short)
    outcome = await ToolExecutor(registry).execute(call({"query": "wait"}), context())
    assert outcome.error is not None and outcome.error.category is ToolErrorCategory.TIMEOUT


@pytest.mark.anyio
async def test_query_and_evidence_are_never_written_to_tool_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    query = "PRIVATE RAW QUERY MARKER"
    body = "PRIVATE RAW EVIDENCE MARKER"
    retriever = FakeRetriever(chunks=(retrieved(body),))
    executor, _ = executor_for(retriever)
    caplog.set_level(logging.INFO)

    outcome = await executor.execute(call({"query": query}), context())

    logs = "\n".join(record.getMessage() + repr(record.__dict__) for record in caplog.records)
    assert outcome.success
    assert query not in logs and body not in logs
    assert "Private title marker" not in logs
