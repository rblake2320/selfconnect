"""
health_monitor.py — Periodic health checks for all services.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None
_status = {"sdk": "down", "yolo": "degraded", "vl": "down", "claude": "down"}


def get_status() -> dict:
    return dict(_status)


async def _check_sdk() -> str:
    try:
        from concurrent.futures import ThreadPoolExecutor
        import asyncio
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as pool:
            wins = await loop.run_in_executor(pool, __import__("self_connect").list_windows)
        return "ok" if wins is not None else "degraded"
    except Exception:
        return "down"


def _check_yolo() -> str:
    """degraded if no custom UI-YOLO model file present; ok if file exists."""
    import os
    from vision_server.config import YOLO_MODEL_PATH
    return "ok" if os.path.isfile(YOLO_MODEL_PATH) else "degraded"


def _check_claude() -> str:
    """ok if SC_TOKEN env var is set (server is auth-enabled); degraded otherwise."""
    import os
    return "ok" if os.environ.get("SC_TOKEN") else "degraded"


async def _check_vl() -> str:
    try:
        import httpx
        from vision_server.config import OLLAMA_URL
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            return "ok" if r.status_code == 200 else "degraded"
    except Exception:
        return "down"


async def _monitor_loop():
    from vision_server.services.event_bus import publish

    while True:
        _status["sdk"] = await _check_sdk()
        _status["vl"] = await _check_vl()
        _status["yolo"] = _check_yolo()
        _status["claude"] = _check_claude()

        await publish("health", dict(_status))
        await asyncio.sleep(5)


async def start():
    global _task
    _task = asyncio.create_task(_monitor_loop())
    logger.info("[health] monitor started")


async def stop():
    global _task
    if _task:
        _task.cancel()
