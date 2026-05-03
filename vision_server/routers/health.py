"""
routers/health.py — Health check endpoint.
"""
from fastapi import APIRouter
from vision_server.models.schemas import HealthStatus

router = APIRouter()


@router.get("/health", response_model=HealthStatus)
async def get_health():
    """Return live health status of all services."""
    from vision_server.services.health_monitor import get_status
    return get_status()
