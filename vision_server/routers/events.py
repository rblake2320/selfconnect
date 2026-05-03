"""
routers/events.py — Multiplexed WebSocket for all event channels.
All channels (detections, log, queue, health, windows, vl) flow through here.
"""
import asyncio
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    """
    Multiplexed event WebSocket. Messages are JSON:
      { "channel": "detections"|"log"|"queue"|"health"|"windows"|"vl", "data": ... }
    """
    from vision_server.services.event_bus import subscribe_all, unsubscribe_all

    await websocket.accept()
    logger.info("[ws/events] client connected")

    async def send_event(msg: str):
        try:
            await websocket.send_text(msg)
        except Exception:
            pass

    await subscribe_all(send_event)

    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        logger.info("[ws/events] client disconnected")
    finally:
        await unsubscribe_all(send_event)
