"""Run tests against a private, ephemeral Compose PostgreSQL instance only."""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def manual_http_probe(env: dict[str, str], compose: list[str]) -> None:
    """Verify real HTTP behavior before and after stopping only our test database."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    artifacts = ROOT / ".artifacts"
    artifacts.mkdir(exist_ok=True)
    opener = build_opener(ProxyHandler({}))
    with (artifacts / "phase1-http.log").open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
            ],
            cwd=ROOT,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        def get(path: str) -> tuple[int, object]:
            try:
                with opener.open(f"http://127.0.0.1:{port}/{path}", timeout=5) as response:
                    assert response.headers["X-Request-ID"]
                    return response.status, json.load(response)
            except HTTPError as exc:
                assert exc.headers["X-Request-ID"]
                return exc.code, json.load(exc)

        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    health = get("health")
                    break
                except URLError:
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("HTTP smoke server failed to start.") from None
                    time.sleep(0.1)
            ready = get("ready")
            assert health[0] == 200
            assert ready == (200, {"status": "ready", "dependencies": {"postgresql": "ok"}})
            subprocess.run(
                [*compose, "stop", "postgres-test"], cwd=ROOT, env=env, check=True, timeout=45
            )
            failed = get("ready")
            alive = get("health")
            assert failed == (
                503,
                {"status": "not_ready", "dependencies": {"postgresql": "unavailable"}},
            )
            assert alive[0] == 200
            results = {
                "health": health,
                "ready": ready,
                "database_down_ready": failed,
                "database_down_health": alive,
            }
            (artifacts / "phase1-http.json").write_text(
                json.dumps(results, indent=2), encoding="utf-8"
            )
            print(
                "HTTP probes passed: health=200, ready=200; database down: ready=503, health=200."
            )
        finally:
            process.terminate()
            process.wait(timeout=10)
    logs = (artifacts / "phase1-http.log").read_text(encoding="utf-8")
    assert env["POSTGRES_PASSWORD"] not in logs


def main() -> int:
    """Create run-owned resources; never accept an external database URL or reuse dev data."""
    run_id = uuid4().hex
    project = f"agentopshub-test-{run_id}"
    env = dict(os.environ, PYTHONUTF8="1")
    env.update(
        TEST_POSTGRES_DB=f"agentopshub_test_{run_id}",
        TEST_POSTGRES_USER="agentopshub_test",
        TEST_POSTGRES_PASSWORD=secrets.token_urlsafe(32),
    )
    compose = ["docker", "compose", "-f", str(ROOT / "docker-compose.test.yml"), "-p", project]
    try:
        subprocess.run([*compose, "config", "--quiet"], cwd=ROOT, env=env, check=True, timeout=15)
        subprocess.run(
            [*compose, "up", "-d", "--wait", "--wait-timeout", "60"],
            cwd=ROOT,
            env=env,
            check=True,
            timeout=180,
        )
        binding = subprocess.check_output(
            [*compose, "port", "postgres-test", "5432"], cwd=ROOT, env=env, text=True, timeout=15
        ).strip()
        port = str(int(binding.rsplit(":", 1)[-1]))
        env.update(
            POSTGRES_HOST="127.0.0.1",
            POSTGRES_PORT=port,
            POSTGRES_DB=env["TEST_POSTGRES_DB"],
            POSTGRES_USER=env["TEST_POSTGRES_USER"],
            POSTGRES_PASSWORD=env["TEST_POSTGRES_PASSWORD"],
            AGENTOPSHUB_TEST_RUN_ID=run_id,
            AGENTOPSHUB_TEST_PORT=port,
            AGENTOPSHUB_ENVIRONMENT="test",
        )
        print(f"Test interpreter: {sys.executable}", flush=True)
        artifacts = ROOT / ".artifacts"
        artifacts.mkdir(exist_ok=True)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--cov=app",
                "--cov-report=term-missing",
                "--cov-report=json:.artifacts/phase1-coverage.json",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=180,
        )
        (artifacts / "phase1-pytest.txt").write_text(
            result.stdout + result.stderr, encoding="utf-8"
        )
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        if result.returncode:
            return result.returncode
        manual_http_probe(env, compose)
        return 0
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f"Isolated PostgreSQL validation failed: {type(exc).__name__}.", file=sys.stderr)
        return 1
    finally:
        # The unique project was created by this invocation; it has only tmpfs storage.
        subprocess.run([*compose, "down"], cwd=ROOT, env=env, check=True, timeout=45)


if __name__ == "__main__":
    raise SystemExit(main())
