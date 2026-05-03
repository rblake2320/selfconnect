"""
routers/capture.py — Live capture WebSocket + single-frame REST endpoint.
ASSIGNED TO: Agent B
"""
import asyncio
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import Response

router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/ws/capture")
async def ws_capture(websocket: WebSocket):
    """
    WebSocket endpoint that pushes live JPEG frames as binary messages.
    No auth token required (same-origin WS connection from the dashboard).
    Client receives raw JPEG bytes; use URL.createObjectURL(new Blob([data], {type:'image/jpeg'}))
    """
    from vision_server.services import capture_service

    await websocket.accept()
    logger.info("[ws/capture] client connected")

    # Register this WebSocket as a frame subscriber
    async def send_frame(frame_bytes: bytes):
        try:
            await websocket.send_bytes(frame_bytes)
        except Exception:
            pass

    capture_service.add_frame_subscriber(send_frame)

    try:
        # Keep alive — receive any client messages (ping/pong, attach commands)
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                logger.debug(f"[ws/capture] client message: {msg[:50]}")
            except asyncio.TimeoutError:
                pass  # just keep alive
    except WebSocketDisconnect:
        logger.info("[ws/capture] client disconnected")
    finally:
        capture_service.remove_frame_subscriber(send_frame)


@router.get("/api/capture/{hwnd}")
async def get_capture(hwnd: int):
    """Return a single JPEG snapshot of the specified window."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from vision_server.services.capture_service import _capture_one

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=1) as pool:
        frame = await loop.run_in_executor(pool, _capture_one, hwnd)

    if not frame:
        raise HTTPException(status_code=404, detail=f"Could not capture hwnd={hwnd}")

    return Response(content=frame, media_type="image/jpeg")
