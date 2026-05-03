"""
routers/actions.py — Action queue management.
ASSIGNED TO: Agent C (Phase 4)
"""
from fastapi import APIRouter, HTTPException
from vision_server.models.schemas import QueueItem, ActionRequest, CommandRequest

router = APIRouter()


@router.post("/actions", response_model=QueueItem)
async def enqueue_action(req: ActionRequest):
    """Enqueue a click/type/key action for execution."""
    from vision_server.services.action_queue import enqueue
    return await enqueue(req)


@router.get("/queue", response_model=list[QueueItem])
async def get_queue():
    """Return the current action queue."""
    from vision_server.services.action_queue import get_queue
    return get_queue()


@router.delete("/queue/{item_id}")
async def cancel_action(item_id: str):
    """Cancel a pending queue item."""
    from vision_server.services.action_queue import cancel
    ok = cancel(item_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Queue item {item_id} not found")
    return {"ok": True}


@router.post("/actions/run")
async def run_queue():
    """Start executing the action queue."""
    from vision_server.services.action_queue import run
    await run()
    return {"ok": True}


@router.post("/actions/pause")
async def pause_queue():
    """Pause queue execution."""
    from vision_server.services.action_queue import pause
    pause()
    return {"ok": True}


@router.post("/command")
async def natural_language_command(req: CommandRequest):
    """Parse a natural language command and enqueue appropriate actions."""
    from vision_server.services.action_queue import enqueue_command
    return await enqueue_command(req.text)
