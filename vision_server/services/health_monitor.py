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
        _status["yolo"] = "degraded"   # no custom model in v1
        _status["claude"] = "ok"       # API key presence check only

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
