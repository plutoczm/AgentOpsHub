"""Evaluate only the run-owned PostgreSQL target created by dev.py eval-retrieval."""

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path

from pydantic import SecretStr

from app.core.config import Settings
from app.db.session import Database
from app.evaluation.benchmark import run_benchmark

ROOT = Path(__file__).resolve().parents[1]


def isolated_settings() -> Settings:
    """Reject ordinary application databases and external addresses before connecting."""
    run_id = os.environ.get("AGENTOPSHUB_TEST_RUN_ID", "")
    name = os.environ.get("TEST_POSTGRES_DB", "")
    if (
        not re.fullmatch(r"[a-f0-9]{32}", run_id)
        or name != f"agentopshub_test_{run_id}"
        or os.environ.get("POSTGRES_DB") != name
        or os.environ.get("POSTGRES_HOST") != "127.0.0.1"
        or os.environ.get("POSTGRES_PORT") != os.environ.get("AGENTOPSHUB_TEST_PORT")
    ):
        raise ValueError("Use python scripts/dev.py eval-retrieval for an isolated database")
    return Settings(
        _env_file=None,
        environment="test",
        database_host="127.0.0.1",
        database_port=int(os.environ["AGENTOPSHUB_TEST_PORT"]),
        database_name=name,
        database_user=os.environ["TEST_POSTGRES_USER"],
        database_password=SecretStr(os.environ["TEST_POSTGRES_PASSWORD"]),
    )


async def run() -> None:
    """Write deliberate synthetic artifacts and concise metrics without queries or content."""
    database = Database(isolated_settings())
    try:
        evidence = await run_benchmark(database, ROOT)
    finally:
        await database.close()
    output = evidence.model_dump(mode="json")
    output["git_sha"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()
    output["working_tree_dirty"] = bool(
        subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            text=True,
        ).strip()
    )
    artifact = ROOT / ".artifacts/phase6-retrieval.json"
    artifact.parent.mkdir(exist_ok=True)
    artifact.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Retriever: {evidence.retriever}; PostgreSQL: {evidence.postgres_version}")
    print(f"Corpus: {evidence.unchanged_documents} documents; repeated rankings/metrics identical")
    for report in evidence.reports:
        print(f"Dataset: {report.dataset}; queries: {report.query_count}")
        print(report.groups[0].model_dump_json())
        print("Local sequential latency:", report.latency.model_dump_json())
    print("Detailed synthetic category/ranking evidence: .artifacts/phase6-retrieval.json")


def main() -> int:
    """Suppress raw validation/database exception payloads at the command boundary."""
    try:
        asyncio.run(run())
    except Exception:
        print("Retrieval benchmark failed; verify isolated setup and test results.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
