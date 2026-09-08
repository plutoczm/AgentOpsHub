"""Readiness checks PostgreSQL without changing the /health liveness contract."""

from fastapi import APIRouter, Request, Response

from app.db.session import Database
from app.schemas.readiness import ReadinessDependencies, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get(
    "/ready", response_model=ReadinessResponse, responses={503: {"model": ReadinessResponse}}
)
async def ready(request: Request, response: Response) -> ReadinessResponse:
    """Return 200 for SELECT 1 success or a deterministic, credential-free 503."""
    database: Database = request.app.state.database
    available = await database.ready()
    response.status_code = 200 if available else 503
    return ReadinessResponse(
        status="ready" if available else "not_ready",
        dependencies=ReadinessDependencies(postgresql="ok" if available else "unavailable"),
    )
