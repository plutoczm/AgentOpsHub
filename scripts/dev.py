"""Cross-platform project commands; invoke from PowerShell, cmd or a POSIX shell."""

from __future__ import annotations

import argparse
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
        rb"(?:sk-[A-Za-z0-9_-]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
        rb"(?im:^[ \t]*(?:[A-Z0-9_]*API_KEY|[A-Z0-9_]*SECRET|[A-Z0-9_]*PASSWORD)"
        rb"[ \t]*=[ \t]*[^\s#]+))"
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
        if pattern.search(content):
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
        "hooks": ["pre-commit", "run", "--all-files"],
        "hooks-install": ["pre-commit", "install"],
    }
    try:
        if args.command == "init-env":
            init_env()
        elif args.command == "secrets":
            check_staged_secrets()
        elif args.command == "sync":
            run([uv_command(), "sync", "--locked"])
        elif args.command == "serve":
            from app.core.config import load_settings

            settings = load_settings()
            run(
                [
                    uv_command(),
                    "run",
                    "--locked",
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
                run([uv_command(), "run", "--locked", *commands[command]])
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
            run([uv_command(), "run", "--locked", *commands[args.command]])
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
