"""Cross-platform project commands; invoke from PowerShell, cmd or a POSIX shell."""

from __future__ import annotations

import argparse
import ast
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]


def run(args: list[str]) -> None:
    """Run an argument vector without a shell and fail on nonzero exit status."""
    subprocess.run(args, cwd=ROOT, check=True)


def sync_environment(*, dry_run: bool = False) -> None:
    """Sync the lock into this interpreter's isolated environment, preserving Conda tools.

    Explicit targeting avoids uv silently selecting the legacy project .venv.
    Base Conda and unisolated system Python are rejected before any mutation.
    """
    prefix = Path(sys.prefix).resolve()
    conda = (prefix / "conda-meta").is_dir()
    if conda and (prefix / "conda-meta/history").is_file():
        if (prefix / "condabin").is_dir() or (prefix / "Scripts/conda.exe").is_file():
            raise RuntimeError("Refusing dependency installation into base Conda.")
    elif sys.prefix == sys.base_prefix:
        raise RuntimeError("Use an isolated Conda environment or virtual environment first.")
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(prefix))
    command = [
        uv_command(),
        "sync",
        "--locked",
        "--inexact",
        "--python",
        sys.executable,
        "--no-python-downloads",
    ]
    if dry_run:
        command.append("--dry-run")
    print(f"Dependency target: {sys.executable}", flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def uv_command() -> str:
    """Find uv on PATH or in this workspace's optional bootstrap tool directory."""
    installed = shutil.which("uv")
    local = ROOT / ".tools/uv-package/bin/uv.exe"
    if installed:
        return installed
    if local.is_file():
        return str(local)
    raise RuntimeError("uv was not found. Install uv using the official instructions in README.md.")


def init_env() -> None:
    """Create a local dotenv with a random development password, never overwrite."""
    target = ROOT / ".env"
    content = (ROOT / ".env.example").read_text(encoding="utf-8")
    content = content.replace("replace-with-a-local-random-password", secrets.token_urlsafe(32))
    try:
        with target.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    except FileExistsError:
        print(".env already exists; preserved existing configuration.")
        return
    print("Created .env with a random local password. Do not commit it.")


def python_has_credential_literal(content: bytes) -> bool:
    """Find hardcoded Python credential assignments/arguments without flagging lookups."""
    tree = ast.parse(content)
    sensitive = re.compile(r"(?:.*api_key|.*secret|.*password)$", re.IGNORECASE)
    for node in ast.walk(tree):
        names: list[str] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
            value = node.value
        elif isinstance(node, ast.keyword) and node.arg is not None:
            names = [node.arg]
            value = node.value
        if not any(sensitive.fullmatch(name) for name in names):
            continue
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "SecretStr"
            and value.args
        ):
            value = value.args[0]
        if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value:
            return True
    return False


def check_staged_secrets() -> None:
    """Reject local credential files and common credential forms from the Git index.

    This focused safety net is not a complete secret detector. Review the staged
    diff before every commit. File content and matched secrets are never printed.
    """
    names = (
        subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"], cwd=ROOT
        )
        .decode("utf-8")
        .split("\0")
    )
    pattern = re.compile(
        rb"(?:sk-[A-Za-z0-9_-]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
    )
    assignments = re.compile(
        rb"(?im:^[ \t]*(?:[A-Z0-9_]*API_KEY|[A-Z0-9_]*SECRET|[A-Z0-9_]*PASSWORD)"
        rb"[ \t]*=[ \t]*[^\s#]+)"
    )
    failed: list[str] = []
    for name in filter(None, names):
        path = Path(name)
        if (path.name == ".env" or path.name.startswith(".env.")) and path.name != ".env.example":
            failed.append(name)
            continue
        if path.suffix.lower() in {".pem", ".key"}:
            failed.append(name)
            continue
        content = subprocess.check_output(["git", "show", f":{name}"], cwd=ROOT)
        if name == ".env.example":
            content = content.replace(
                b"POSTGRES_PASSWORD=replace-with-a-local-random-password", b""
            )
        if path.suffix == ".py":
            try:
                has_assignment = python_has_credential_literal(content)
            except (SyntaxError, UnicodeDecodeError):
                has_assignment = bool(assignments.search(content))
        else:
            has_assignment = bool(assignments.search(content))
        if pattern.search(content) or has_assignment:
            failed.append(name)
    if failed:
        raise RuntimeError("Potential credentials in staged files: " + ", ".join(failed))
    print("Staged secret checks passed.")


def infra_check() -> None:
    """Check database/cache commands and Qdrant readiness with bounded waiting."""
    run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "sh",
            "-c",
            'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
        ]
    )
    pong = subprocess.check_output(
        ["docker", "compose", "exec", "-T", "redis", "redis-cli", "ping"], cwd=ROOT
    ).strip()
    if pong != b"PONG":
        raise RuntimeError("Redis did not return PONG.")
    binding = subprocess.check_output(
        ["docker", "compose", "port", "qdrant", "6333"], cwd=ROOT, text=True
    ).strip()
    port = int(binding.rsplit(":", 1)[-1])
    # Local probes must not pass through Windows/system HTTP proxies.
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 45
    while True:
        try:
            with opener.open(f"http://127.0.0.1:{port}/readyz", timeout=2) as response:
                if response.status == 200:
                    break
        except (URLError, TimeoutError):
            if time.monotonic() >= deadline:
                raise RuntimeError("Qdrant did not become ready within 45 seconds.") from None
        if time.monotonic() >= deadline:
            raise RuntimeError("Qdrant readiness probe failed.")
        time.sleep(1)
    print("Infrastructure probes passed: PostgreSQL, Redis, Qdrant.")


def main() -> int:
    """Dispatch a documented command with useful, bounded error handling."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "init-env",
            "sync",
            "sync-check",
            "env-info",
            "lock",
            "test-integration",
            "eval-retrieval",
            "eval-knowledge-agent",
            "db-upgrade",
            "db-current",
            "serve",
            "test",
            "lint",
            "format",
            "format-check",
            "typecheck",
            "check",
            "hooks",
            "hooks-install",
            "secrets",
            "compose-check",
            "infra-up",
            "infra-check",
            "infra-down",
        ],
    )
    args = parser.parse_args()
    os.chdir(ROOT)
    commands = {
        "test": ["pytest", "--cov=app", "--cov-report=term-missing"],
        "lint": ["ruff", "check", "."],
        "format": ["ruff", "format", "."],
        "format-check": ["ruff", "format", "--check", "."],
        "typecheck": ["mypy"],
        "hooks": ["pre_commit", "run", "--all-files"],
        "hooks-install": ["pre_commit", "install"],
    }
    try:
        if args.command == "eval-retrieval":
            run([sys.executable, str(ROOT / "scripts/test_postgres.py"), "--eval-retrieval"])
        elif args.command == "eval-knowledge-agent":
            run(
                [
                    sys.executable,
                    str(ROOT / "scripts/test_postgres.py"),
                    "--eval-knowledge-agent",
                ]
            )
        elif args.command == "test-integration":
            run([sys.executable, str(ROOT / "scripts/test_postgres.py")])
        elif args.command == "lock":
            run([uv_command(), "lock", "--python", sys.executable, "--no-python-downloads"])
        elif args.command == "db-upgrade":
            run([sys.executable, "-m", "alembic", "upgrade", "head"])
        elif args.command == "db-current":
            run([sys.executable, "-m", "alembic", "current"])
        elif args.command == "init-env":
            init_env()
        elif args.command == "secrets":
            check_staged_secrets()
        elif args.command in {"sync", "sync-check"}:
            sync_environment(dry_run=args.command == "sync-check")
        elif args.command == "env-info":
            print(f"Python: {sys.version}")
            print(f"Executable: {sys.executable}")
            print(f"Prefix: {sys.prefix}")
        elif args.command == "serve":
            from app.core.config import load_settings

            settings = load_settings()
            run(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:create_app",
                    "--factory",
                    "--host",
                    settings.host,
                    "--port",
                    str(settings.port),
                    "--no-access-log",
                ]
            )
        elif args.command == "check":
            for command in ("lint", "format-check", "typecheck", "test"):
                run([sys.executable, "-m", *commands[command]])
            run(["git", "diff", "--check"])
            run(["git", "diff", "--cached", "--check"])
        elif args.command == "compose-check":
            run(["docker", "compose", "--env-file", ".env.example", "config", "--quiet"])
        elif args.command == "infra-up":
            run(["docker", "compose", "up", "-d", "--wait", "--wait-timeout", "90"])
            infra_check()
        elif args.command == "infra-check":
            infra_check()
        elif args.command == "infra-down":
            run(["docker", "compose", "down"])
        else:
            run([sys.executable, "-m", *commands[args.command]])
    except subprocess.CalledProcessError as exc:
        print(f"Command failed (exit {exc.returncode}).", file=sys.stderr)
        return exc.returncode if exc.returncode > 0 else 1
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
