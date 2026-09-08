# Findings

- Workspace initially contains only .git; no commits, remotes or author identity.
- Docker Desktop client 29.6.1, Compose v5.3.0; Linux engine unavailable.
- Available Python: Anaconda 3.13.9, outside PATH; no usable PATH uv/python.
- No relevant prior memory entries found.
- Design and implementation are original; no existing project code copied.

Official reference material consulted for API/operational behavior:
- https://docs.astral.sh/uv/concepts/projects/sync/ — locked sync.
- https://docs.astral.sh/uv/guides/install-python/ — managed Python.
- https://fastapi.tiangolo.com/advanced/events/ — lifespan context.
- https://qdrant.tech/documentation/installation/ — storage requirements.
- https://qdrant.tech/documentation/common-errors/ — Windows named volumes.

- https://www.starlette.io/testclient/ confirms httpx2 is the current test client dependency.

## Phase 1 discovery

- Starting commit and clean working tree verified; legacy .venv is not tracked.
- Conda 25.11.1, uv 0.12.10. Dedicated environment created with Python 3.12.14.
- uv dry-run with UV_PROJECT_ENVIRONMENT=current prefix, --locked --inexact
  --python=current executable --no-python-downloads selected the Conda environment.
- Official reference: https://docs.astral.sh/uv/concepts/projects/config/
  explains explicit environment targeting and exact-sync removal hazards.
- SQLAlchemy async sessions: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- Alembic async migration guidance: https://alembic.sqlalchemy.org/en/latest/cookbook.html

## Phase 2 official references

- https://pydantic.dev/docs/httpx2/advanced/transports/ — MockTransport seam.
- https://pydantic.dev/docs/httpx2/advanced/timeouts/ — connect/read/write/pool timeouts.
- https://api-docs.deepseek.com/ — configurable OpenAI-compatible base URL.
- https://api-docs.deepseek.com/api/create-chat-completion/ — chat wire format.
- https://docs.modelstudio.console.alibabacloud.com/en/model-studio/qwen-structured-output
  — Qwen compatible API and region/workspace-specific endpoint configuration.

## Phase 3 discovery

- TicketRepository already enforces tenant predicates and bounded status-filtered list.
- Database.transaction owns commit/rollback; repository create only flushes.
- Read-only service can use Database.sessions without commit.
- LLM ToolCall arguments are parsed JSON objects; runtime must validate business schema.
- JsonFormatter uses event/field allowlists; runtime adds metadata only.
- Existing roadmap numbers conflict with the new request; update Phase 3/4/5/6 to requested sequence.

- Final design uses contracts.py to co-locate generic Tool and the small heterogeneous Protocol;
  no separate adapters/policy framework needed. Existing LLM schemas are generated directly.
- No priority search filter: reuse the existing status/limit API plus tenant-scoped exact ID.
- Service validates create DTO before commit. Generic executor output validation cannot retroactively
  undo arbitrary handler commits; timeout/commit ambiguity is documented, no hidden retry.
- Full isolated PostgreSQL validation: 266 passed; all new service/runtime paths covered.
