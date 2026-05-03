"""
action_queue.py — FIFO action queue with state machine.
ASSIGNED TO: Agent C

State machine per item: pending -> running -> done | failed
Execution maps kind to SDK call:
  click  -> click_at(x, y) or click_window(target, cx, cy)
  type   -> send_string(target, text)
  key    -> send_keys(*keys)
  wait   -> asyncio.sleep(seconds)

Focus verification: verify active window has focus before send_string
Interruptible: check _paused flag between steps
"""
import asyncio
import logging
import uuid
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

_queue: deque = deque()
_history: list = []
_running = False
_paused = False
_task: Optional[asyncio.Task] = None


def get_queue() -> list:
    return list(_queue) + [i for i in _history if i["state"] in ("running",)]


async def enqueue(req) -> dict:
    item = {
        "id": str(uuid.uuid4())[:8],
        "kind": req.kind,
        "target": req.target or "",
        "value": req.value or "",
        "state": "pending",
    }
    _queue.append(item)

    from vision_server.services.event_bus import publish
    await publish("queue", _get_all())
    await publish("log", {
        "t": _ts(),
        "kind": req.kind,
        "status": "pending",
        "msg": f"Queued {req.kind}: {req.target or req.value}",
    })
    return item


def cancel(item_id: str) -> bool:
    for item in _queue:
        if item["id"] == item_id:
            _queue.remove(item)
            return True
    return False


async def run():
    global _running, _paused, _task
    _paused = False
    if _running:
        return
    _running = True
    _task = asyncio.create_task(_execute_loop())


def pause():
    global _paused
    _paused = True


async def _execute_loop():
    global _running
    from vision_server.services.event_bus import publish

    while _queue and not _paused:
        item = _queue[0]
        item["state"] = "running"
        await publish("queue", _get_all())

        try:
            await _execute_item(item)
            item["state"] = "done"
            await publish("log", {"t": _ts(), "kind": item["kind"], "status": "ok",
                                   "msg": f"Done: {item['target'] or item['value']}"})
        except Exception as e:
            item["state"] = "failed"
            logger.error(f"[queue] action failed: {e}")
            await publish("log", {"t": _ts(), "kind": item["kind"], "status": "fail",
                                   "msg": f"Failed: {e}"})

        _queue.popleft()
        _history.append(item)
        await publish("queue", _get_all())

        from vision_server.config import ACTION_STEP_DELAY
        await asyncio.sleep(ACTION_STEP_DELAY)

    _running = False


async def _execute_item(item: dict):
    """Execute a single queue item via SelfConnect SDK."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from concurrent.futures import ThreadPoolExecutor

    loop = asyncio.get_event_loop()
    kind = item["kind"]

    if kind == "click":
        # value is "x,y" screen coords
        parts = item["value"].split(",")
        if len(parts) == 2:
            x, y = int(parts[0].strip()), int(parts[1].strip())
            from self_connect import click_at
            await loop.run_in_executor(None, click_at, x, y)
        else:
            raise ValueError(f"click value must be 'x,y', got: {item['value']}")

    elif kind == "type":
        from vision_server.config import active_hwnd, ACTION_FOCUS_VERIFY
        from self_connect import send_string, focus_window
        if ACTION_FOCUS_VERIFY and active_hwnd:
            await loop.run_in_executor(None, focus_window, active_hwnd)
            await asyncio.sleep(0.1)
        await loop.run_in_executor(None, send_string, active_hwnd, item["value"])

    elif kind == "key":
        from self_connect import send_keys
        keys = item["value"].split("+")
        await loop.run_in_executor(None, send_keys, *keys)

    elif kind == "wait":
        secs = float(item["value"]) if item["value"] else 1.0
        await asyncio.sleep(secs)

    else:
        raise ValueError(f"Unknown action kind: {kind}")


async def enqueue_command(text: str) -> dict:
    """Parse natural language command and enqueue appropriate action."""
    text_lower = text.lower().strip()

    # Simple pattern matching — expand as needed
    if text_lower.startswith("click "):
        label = text[6:].strip()
        # For now enqueue as a type request for human review
        from vision_server.models.schemas import ActionRequest
        req = ActionRequest(kind="click", target=label, value="0,0")
        return await enqueue(req)

    elif text_lower.startswith("type "):
        value = text[5:].strip()
        from vision_server.models.schemas import ActionRequest
        req = ActionRequest(kind="type", target="", value=value)
        return await enqueue(req)

    else:
        from vision_server.models.schemas import ActionRequest
        req = ActionRequest(kind="type", target="", value=text)
        return await enqueue(req)


def _get_all() -> list:
    return [dict(i) for i in _queue]


def _ts() -> str:
    from datetime import datetime
    n = datetime.now()
    return n.strftime("%H:%M:%S.") + f"{n.microsecond // 1000:03d}"
