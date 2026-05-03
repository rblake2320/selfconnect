"""
macro_recorder.py — Record and replay action sequences.
ASSIGNED TO: Agent C
"""
import json
import logging
import os
import time
from datetime import datetime

logger = logging.getLogger(__name__)

_recording = False
_steps: list = []
_start_time: float = 0.0


def start():
    global _recording, _steps, _start_time
    _recording = True
    _steps = []
    _start_time = time.monotonic()
    logger.info("[macro] recording started")


def stop() -> list:
    global _recording
    _recording = False
    logger.info(f"[macro] recording stopped — {len(_steps)} steps")
    return list(_steps)


def get_steps() -> list:
    return list(_steps)


def record_step(action: str, target: str, value: str):
    """Called by action_queue after each executed step during recording."""
    if not _recording:
        return
    elapsed = time.monotonic() - _start_time
    _steps.append({
        "t": f"+{elapsed:.3f}s",
        "action": action,
        "target": target,
        "value": value,
    })


async def replay():
    """Replay recorded steps with original timing."""
    if not _steps:
        return

    from vision_server.services import action_queue

    t_prev = 0.0
    for step in _steps:
        # Parse relative time
        t_str = step["t"].lstrip("+").rstrip("s")
        t_curr = float(t_str)
        delay = t_curr - t_prev
        if delay > 0:
            import asyncio
            await asyncio.sleep(delay)
        t_prev = t_curr

        from vision_server.models.schemas import ActionRequest
        req = ActionRequest(
            kind=step["action"],
            target=step.get("target", ""),
            value=step.get("value", ""),
        )
        await action_queue.enqueue(req)

    await action_queue.run()


def export_json() -> str:
    """Export recorded steps to a JSON file."""
    from vision_server.config import MACRO_EXPORT_DIR
    os.makedirs(MACRO_EXPORT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(MACRO_EXPORT_DIR, f"macro_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "schema": "selfconnect-macro-v1",
            "recorded": ts,
            "steps": _steps,
        }, f, indent=2)
    logger.info(f"[macro] exported to {path}")
    return path


def load_json(path: str) -> list:
    """Load a previously exported macro JSON file into the active step buffer."""
    global _steps
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("schema") != "selfconnect-macro-v1":
        raise ValueError(f"Unsupported macro schema: {data.get('schema')!r}")
    steps = data.get("steps", [])
    if not isinstance(steps, list):
        raise ValueError("Macro steps must be a list")
    _steps = [
        {
            "t": str(step.get("t", "+0.000s")),
            "action": str(step.get("action", "")),
            "target": str(step.get("target", "")),
            "value": str(step.get("value", "")),
        }
        for step in steps
    ]
    return list(_steps)
