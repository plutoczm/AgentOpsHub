"""Process liveness endpoint; no network dependencies are contacted."""

from fastapi import APIRouter, Request

from app import __version__
from app.core.config import Settings
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Return liveness and version, even when optional infrastructure is down."""
    settings: Settings = request.app.state.settings
    return HealthResponse(service=settings.app_name, version=__version__)
