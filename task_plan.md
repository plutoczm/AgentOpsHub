# Phase 0 task plan

Scope: architecture and bootstrap only. Phase 1 has not started.

- [x] Inspect workspace and publish A-D proposal before implementation.
- [x] Architecture, roadmap, directory structure and configuration.
- [x] Minimal API, logging, exception handling and boundary tests.
- [x] Tooling, Compose, CI configuration and Windows instructions.
- [x] Execute checks and record actual evidence in progress.md.
- [x] Prepare the reviewed Phase 0 change for initial Git commit.

Commit execution is the final operation; its authoritative result is Git history.
User supplied author Jeremy and selected the MIT license. No remote push is requested.

Decisions: modular monolith, Python 3.12 baseline, uv lock, strict mypy,
metadata-only JSON logs, Docker named volumes and no live LLM dependency.
Runtime/tool installation stays in .venv and ignored .tools; global PATH is unchanged.

Resolved issues: unusable PATH Python, unavailable Docker Linux engine, missing uv,
YAML quoting, formatting, Starlette httpx2 migration, AnyIO compatibility ceiling,
and Windows proxy interception of loopback readiness probes. See progress.md.
