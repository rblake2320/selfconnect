"""
routers/windows.py — Window discovery and attachment.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, HTTPException
from vision_server.models.schemas import WindowInfo, AttachRequest
from vision_server import config

router = APIRouter()


def _to_window_info(w, active_hwnd: int) -> WindowInfo:
    return WindowInfo(
        hwnd=hex(w.hwnd),
        title=w.title[:120],
        exe=w.exe_name or "unknown",
        active=(w.hwnd == active_hwnd),
    )


@router.get("/windows", response_model=list[WindowInfo])
async def list_windows_endpoint():
    """Return all visible windows from SelfConnect SDK."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from self_connect import list_windows

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=1) as pool:
        wins = await loop.run_in_executor(pool, list_windows)

    return [_to_window_info(w, config.active_hwnd) for w in wins]


@router.post("/windows/{hwnd}/attach", response_model=WindowInfo)
async def attach_window(hwnd: int):
    """Set the active window for capture and detection."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from self_connect import list_windows

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=1) as pool:
        wins = await loop.run_in_executor(pool, list_windows)

    win = next((w for w in wins if w.hwnd == hwnd), None)
    if not win:
        raise HTTPException(status_code=404, detail=f"Window hwnd={hwnd} not found")

    config.active_hwnd = hwnd

    # Notify capture service to switch target
    from vision_server.services import capture_service
    capture_service.set_active_hwnd(hwnd)

    from vision_server.services.event_bus import publish
    await publish("windows", [_to_window_info(w, hwnd) for w in wins])
    await publish("log", {
        "t": __import__("datetime").datetime.now().strftime("%H:%M:%S.%f")[:-3],
        "kind": "system",
        "status": "ok",
        "msg": f"Attached to {win.title[:50]} (hwnd={hwnd})",
    })

    return _to_window_info(win, hwnd)
