"""
routers/search.py — nvclip semantic search stub.
Returns 501 until nvclip NIM container is configured.
"""
from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.post("/search")
async def semantic_search(query: dict):
    """Semantic element search via nvclip NIM (not configured in v1)."""
    raise HTTPException(
        status_code=501,
        detail="nvclip semantic search not configured. See docs/DEPLOY.md for setup instructions.",
    )
