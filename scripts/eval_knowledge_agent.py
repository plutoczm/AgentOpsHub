"""Run the versioned Agent-context evaluation on a run-owned test database only."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import func, select

from app.agents import AgentRunContext, AgentRunRequest, AgentRuntime
from app.core.config import Settings
from app.db.models import Tenant, Ticket
from app.db.session import Database
from app.knowledge.context import KnowledgeContextPolicy
from app.knowledge.models import (
    ChunkingConfig,
    DocumentInput,
    IngestionConfig,
    KnowledgeIngestionContext,
)
from app.llm.models import (
    DeploymentType,
    LLMRequest,
    LLMResponse,
    Message,
    Role,
    ToolCall,
)
from app.retrieval.errors import RetrievalBackendError
from app.retrieval.models import KnowledgeRetrievalContext, KnowledgeSearchRequest, RetrievalResult
from app.retrieval.postgres import PostgresFTSRetriever
from app.retrieval.protocols import KnowledgeRetriever
from app.services.knowledge import KnowledgeIngestionService
from app.tools.builtin import build_tool_registry
from app.tools.executor import ToolExecutor

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation/knowledge-agent/v1"
TENANTS = {
    "a": UUID("7e7f1f7a-7c12-4e18-9b06-4c6d1e1a0001"),
    "b": UUID("7e7f1f7a-7c12-4e18-9b06-4c6d1e1a0002"),
}
RETRIEVER_NAME = "postgres-fts-simple-cd-v1"

type GatewayStep = Message | Callable[[LLMRequest], Awaitable[Message]]


class ScriptedGateway:
    """Return deterministic tool decisions while still exercising AgentRuntime's graph."""

    def __init__(self, steps: list[GatewayStep]) -> None:
        """Keep a private fixed response sequence and record requests for assertions."""
        self.steps = list(steps)
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Record an isolated request and return one fixed, schema-valid response."""
        self.requests.append(request.model_copy(deep=True))
        step = self.steps.pop(0)
        message = await step(request) if callable(step) else step
        return LLMResponse(
            message=message,
            finish_reason="tool_calls" if message.tool_calls else "stop",
            provider="phase7a-script",
            model="deterministic",
            deployment_type=DeploymentType.PRIVATE,
            latency_ms=0,
            attempts=(),
        )


class FailingRetriever:
    """Deterministically exercise the safe backend-failure path in the real tool loop."""

    name = "test-failing-retriever-v1"

    async def search(
        self,
        context: KnowledgeRetrievalContext,
        request: KnowledgeSearchRequest,
    ) -> RetrievalResult:
        """Raise the typed backend failure without connecting to external state."""
        raise RetrievalBackendError()


def isolated_settings() -> Settings:
    """Reject ordinary application databases and external hosts before connecting."""
    run_id = os.environ.get("AGENTOPSHUB_TEST_RUN_ID", "")
    name = os.environ.get("TEST_POSTGRES_DB", "")
    if (
        not re.fullmatch(r"[a-f0-9]{32}", run_id)
        or name != f"agentopshub_test_{run_id}"
        or os.environ.get("POSTGRES_DB") != name
        or os.environ.get("POSTGRES_HOST") != "127.0.0.1"
        or os.environ.get("POSTGRES_PORT") != os.environ.get("AGENTOPSHUB_TEST_PORT")
    ):
        raise ValueError("Use python scripts/dev.py eval-knowledge-agent for an isolated database")
    return Settings(
        _env_file=None,
        environment="test",
        database_host="127.0.0.1",
        database_port=int(os.environ["AGENTOPSHUB_TEST_PORT"]),
        database_name=name,
        database_user=os.environ["TEST_POSTGRES_USER"],
        database_password=SecretStr(os.environ["TEST_POSTGRES_PASSWORD"]),
    )


async def seed_corpus(database: Database, manifest: dict[str, Any]) -> dict[str, str]:
    """Create fixed synthetic tenants and ingest every versioned fixture through Phase 5."""
    async with database.transaction() as session:
        for key, tenant_id in TENANTS.items():
            session.add(
                Tenant(
                    id=tenant_id,
                    slug=f"phase7a-agent-{key}",
                    name=f"Synthetic Agent Evaluation {key}",
                )
            )
        await session.flush()

    service = KnowledgeIngestionService(
        database,
        IngestionConfig(chunking=ChunkingConfig(max_chars=256, overlap_chars=32)),
    )
    corpus_hashes: dict[str, str] = {}

    async def ingest(
        tenant: str,
        namespace: str,
        source_key: str,
        relative_path: str,
        title: str,
    ) -> None:
        path = _fixture_path(relative_path)
        content = path.read_bytes()
        corpus_hashes[f"{source_key}:{title}"] = hashlib.sha256(content).hexdigest()
        await service.ingest(
            DocumentInput(
                source_key=source_key,
                title=title,
                media_type="text/markdown",
                content=content,
            ),
            KnowledgeIngestionContext(tenant_id=TENANTS[tenant], namespace=namespace),
        )

    for item in manifest["documents"]:
        await ingest(
            item["tenant"],
            item["namespace"],
            item["source_key"],
            item["path"],
            item["source_key"],
        )
    for item in manifest["revision_updates"]:
        await ingest(
            item["tenant"],
            item["namespace"],
            item["source_key"],
            item["before_path"],
            f"{item['source_key']}-before",
        )
        await ingest(
            item["tenant"],
            item["namespace"],
            item["source_key"],
            item["after_path"],
            f"{item['source_key']}-after",
        )
    return corpus_hashes


async def evaluate_case(
    database: Database,
    case: dict[str, Any],
    real_retriever: PostgresFTSRetriever,
) -> dict[str, Any]:
    """Run one scripted decision through AgentRuntime, ToolExecutor and the typed tool."""
    behavior = case.get("behavior", "answer")
    policy = KnowledgeContextPolicy(
        max_total_evidence_chars=case.get("max_total_evidence_chars", 6000)
    )
    retriever: KnowledgeRetriever = (
        FailingRetriever() if behavior == "retriever_failure" else real_retriever
    )
    registry = build_tool_registry(
        database,
        knowledge_retriever=retriever,
        knowledge_policy=policy,
    )

    async def after_search(request: LLMRequest) -> Message:
        tool_message = request.messages[-1]
        if tool_message.role is not Role.TOOL or tool_message.content is None:
            raise RuntimeError("Agent did not return the knowledge tool result")
        outcome = json.loads(tool_message.content)
        if behavior == "retriever_failure":
            return _answer("The knowledge backend failed safely.")
        if outcome.get("success") is not True:
            raise RuntimeError("Knowledge search unexpectedly failed")
        data = outcome["data"]
        if behavior == "attempt_write":
            return Message(
                role=Role.ASSISTANT,
                tool_calls=(
                    ToolCall(
                        id="phase7a-write-attempt",
                        name="ticket_create",
                        arguments={"title": "Synthetic denied evaluation write"},
                    ),
                ),
            )
        if data["status"] == "no_evidence":
            return _answer("Insufficient evidence.")
        source_key = data["evidence"][0]["source_key"]
        return _answer(f"Evidence source: {source_key}")

    async def after_write_denial(request: LLMRequest) -> Message:
        tool_message = request.messages[-1]
        if tool_message.role is not Role.TOOL or tool_message.content is None:
            raise RuntimeError("Agent did not return the write tool result")
        result = json.loads(tool_message.content)
        if result.get("error", {}).get("category") != "policy":
            raise RuntimeError("Default-deny write policy did not reject the attempted write")
        return _answer("The requested write was denied.")

    tool_call = Message(
        role=Role.ASSISTANT,
        tool_calls=(
            ToolCall(
                id="phase7a-knowledge-search",
                name="knowledge_search",
                arguments={"query": case["query"]},
            ),
        ),
    )
    steps: list[GatewayStep] = [tool_call, after_search]
    if behavior == "attempt_write":
        steps.append(after_write_denial)
    gateway = ScriptedGateway(steps)
    runtime = AgentRuntime(gateway, registry, ToolExecutor(registry))
    run_result = await runtime.run(
        AgentRunRequest(
            user_message=case.get("user_message", "Find the relevant synthetic evidence."),
            route="phase7a-evaluation",
        ),
        AgentRunContext(
            tenant_id=TENANTS[case["tenant"]],
            knowledge_namespace=case["namespace"],
        ),
    )
    first_tool = _tool_payload(gateway.requests[1])
    tool_data = first_tool.get("data", {})
    tool_error = first_tool.get("error", {}).get("category")
    evidence = tool_data.get("evidence", [])
    source_keys = [entry["source_key"] for entry in evidence]
    denied = False
    if behavior == "attempt_write":
        denial_payload = _tool_payload(gateway.requests[2])
        denied = denial_payload.get("error", {}).get("category") == "policy"
    tickets_created = await _ticket_count(database, TENANTS[case["tenant"]])
    expected_source = case.get("expected_source_key")
    expected_status = case.get("expected_status")
    passed = True
    if "expected_tool_error" in case:
        passed = tool_error == case["expected_tool_error"]
    else:
        passed = tool_data.get("status") == expected_status
        if expected_source is not None:
            passed = passed and expected_source in source_keys
        if case.get("expected_budget_exhausted"):
            passed = passed and bool(tool_data.get("budget_exhausted"))
        if expected_status == "no_evidence":
            passed = passed and "Insufficient evidence" in (run_result.final_message.content or "")
    if behavior == "attempt_write":
        passed = passed and denied and tickets_created == 0 and run_result.failed_tool_count == 1
    return {
        "case_id": case["case_id"],
        "passed": passed,
        "status": tool_data.get("status"),
        "tool_error_category": tool_error,
        "retrieved_count": tool_data.get("retrieved_count", 0),
        "evidence_count": tool_data.get("evidence_count", 0),
        "omitted_count": tool_data.get("omitted_count", 0),
        "budget_exhausted": tool_data.get("budget_exhausted", False),
        "context_fingerprint": tool_data.get("context_fingerprint"),
        "context_policy": policy.model_dump(mode="json"),
        "retriever": tool_data.get("retriever", retriever.name),
        "retrieval_duration_ms": tool_data.get("retrieval_duration_ms", 0),
        "context_assembly_duration_ms": tool_data.get("context_assembly_duration_ms", 0),
        "agent_duration_ms": run_result.duration_ms,
        "model_turn_count": run_result.model_turn_count,
        "tool_calls_seen": run_result.tool_calls_seen,
        "tool_executions": run_result.tool_executions,
        "successful_tool_count": run_result.successful_tool_count,
        "failed_tool_count": run_result.failed_tool_count,
        "write_denied": denied if behavior == "attempt_write" else None,
        "ticket_count": tickets_created if behavior == "attempt_write" else None,
    }


def _tool_payload(request: LLMRequest) -> dict[str, Any]:
    """Parse one internal tool message without persisting its private text."""
    message = request.messages[-1]
    if message.role is not Role.TOOL or message.content is None:
        raise RuntimeError("Expected a tool result message")
    return cast(dict[str, Any], json.loads(message.content))


def _answer(text: str) -> Message:
    return Message(role=Role.ASSISTANT, content=text)


async def _ticket_count(database: Database, tenant_id: UUID) -> int:
    async with database.transaction() as session:
        return int(
            await session.scalar(
                select(func.count()).select_from(Ticket).where(Ticket.tenant_id == tenant_id)
            )
            or 0
        )


def _fixture_path(relative: str) -> Path:
    root = DATASET.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Evaluation fixture path is invalid")
    return path


async def run() -> int:
    """Ingest and evaluate only the isolated, run-owned synthetic PostgreSQL database."""
    manifest_path = _fixture_path("manifest.json")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("version") != "phase7a-knowledge-agent-v1":
        raise ValueError("Unsupported knowledge-agent evaluation dataset")
    database = Database(isolated_settings())
    try:
        corpus_hashes = await seed_corpus(database, manifest)
        real_retriever = PostgresFTSRetriever(database)
        cases = [await evaluate_case(database, case, real_retriever) for case in manifest["cases"]]
    finally:
        await database.close()

    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(
        subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    )
    passed_count = sum(bool(case["passed"]) for case in cases)
    artifact = {
        "dataset_version": manifest["version"],
        "evaluation_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "git_sha": git_sha,
        "working_tree_dirty": dirty,
        "corpus_hashes": dict(sorted(corpus_hashes.items())),
        "retriever_identity": RETRIEVER_NAME,
        "case_count": len(cases),
        "passed_count": passed_count,
        "integration_pass_rate": passed_count / len(cases) if cases else 1.0,
        "cases": cases,
    }
    artifact_path = ROOT / ".artifacts/phase7a-knowledge-agent.json"
    artifact_path.parent.mkdir(exist_ok=True)
    artifact_path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    print(
        f"Dataset {manifest['version']}: {passed_count}/{len(cases)} "
        "deterministic integration cases passed."
    )
    print(f"Evidence artifact: {artifact_path.relative_to(ROOT)}")
    return 0 if passed_count == len(cases) else 1


def main() -> int:
    """Print a safe command-level failure without exposing database, query, or evidence data."""
    try:
        return asyncio.run(run())
    except Exception as exc:
        print(f"Knowledge-agent evaluation failed safely ({type(exc).__name__}).")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
