"""
routers/vl.py — Vision-Language description via Ollama llava.
ASSIGNED TO: Agent B (Phase 4)
"""
from fastapi import APIRouter
from vision_server.models.schemas import VLDescription

router = APIRouter()


@router.post("/vl/describe", response_model=VLDescription)
async def describe_screen():
    """
    Trigger VL description of the current capture frame via Ollama llava.
    On-demand only (not every frame — llava takes 200-2000ms).
    """
    from vision_server.services.vl_service import describe_current_frame
    return await describe_current_frame()
