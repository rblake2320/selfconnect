"""
routers/detections.py — UI element detections.
ASSIGNED TO: Agent C
"""
from fastapi import APIRouter
from vision_server.models.schemas import Detection

router = APIRouter()


@router.get("/detections", response_model=list[Detection])
async def get_detections():
    """Return latest detection results for the active window."""
    from vision_server.services.detection_service import get_latest_detections
    return get_latest_detections()
