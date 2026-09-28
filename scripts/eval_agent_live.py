"""Local preflight and isolated Phase 7B live-agent evaluation harness."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))


def _print_preflight(route: str, mode: str) -> tuple[int, bool]:
    from app.core.config import load_settings
    from app.evaluation.live_agent_runner import preflight_for_settings

    report = preflight_for_settings(load_settings(), route_name=route, mode=mode)
    print(report.model_dump_json(indent=2))
    if report.status.value == "configuration_required":
        print("LIVE PROVIDER CONFIGURATION = REQUIRED")
        print(f"API_KEY_CONFIGURED = {'YES' if report.api_key_configured else 'NO'}")
        return 0, False
    if report.status.value == "blocked":
        print("LIVE EVALUATION PREFLIGHT = BLOCKED")
        return 1, False
    return 0, True


def _isolated_postgres(mode: str) -> int:
    option = {
        "offline": "--eval-agent-offline",
        "smoke": "--eval-agent-live-smoke",
        "measured": "--eval-agent-live-measured",
    }[mode]
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/test_postgres.py"), option],
        cwd=ROOT,
        check=False,
    ).returncode


def main() -> int:
    """Dispatch preflight, isolated offline validation, or explicitly opted-in live modes."""
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--preflight", action="store_const", const="preflight", dest="mode")
    modes.add_argument("--offline", action="store_const", const="offline", dest="mode")
    modes.add_argument("--smoke", action="store_const", const="smoke", dest="mode")
    modes.add_argument("--measured", action="store_const", const="measured", dest="mode")
    modes.add_argument(
        "--run-offline",
        action="store_const",
        const="run-offline",
        dest="mode",
        help=argparse.SUPPRESS,
    )
    modes.add_argument(
        "--run-live-smoke",
        action="store_const",
        const="run-smoke",
        dest="mode",
        help=argparse.SUPPRESS,
    )
    modes.add_argument(
        "--run-live-measured",
        action="store_const",
        const="run-measured",
        dest="mode",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--route", default="agent-eval")
    args = parser.parse_args()
    mode = args.mode or "preflight"
    try:
        if mode == "preflight":
            return _print_preflight(args.route, "smoke")[0]
        if mode == "offline":
            return _isolated_postgres("offline")
        if mode in {"smoke", "measured"}:
            status, ready = _print_preflight(args.route, mode)
            if not ready:
                return status if status else 1
            return _isolated_postgres(mode)
        from app.evaluation.live_agent_runner import run_mode

        runner_mode = {
            "run-offline": "offline",
            "run-smoke": "smoke",
            "run-measured": "measured",
        }[mode]
        return run_mode(runner_mode, route_name=args.route, root=ROOT)
    except Exception as exc:
        print(f"Agent evaluation command failed safely ({type(exc).__name__}).")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
