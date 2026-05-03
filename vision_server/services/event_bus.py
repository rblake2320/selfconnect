"""
event_bus.py — In-process pub/sub for all vision server channels.
Channels: detections, log, queue, health, windows, vl
Subscribers are WebSocket send callables registered by the /ws/events handler.
"""
import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Callable, Any

logger = logging.getLogger(__name__)

# Global event bus instance
_subscribers: dict[str, list[Callable]] = defaultdict(list)
_lock = asyncio.Lock()


async def subscribe(channel: str, callback: Callable) -> None:
    """Register a WebSocket send callback for a channel."""
    async with _lock:
        _subscribers[channel].append(callback)


async def unsubscribe(channel: str, callback: Callable) -> None:
    """Remove a callback (call on WebSocket disconnect)."""
    async with _lock:
        try:
            _subscribers[channel].remove(callback)
        except ValueError:
            pass


async def publish(channel: str, data: Any) -> None:
    """Publish data to all subscribers of a channel."""
    msg = json.dumps({"channel": channel, "data": data})
    dead = []
    async with _lock:
        callbacks = list(_subscribers[channel])
    for cb in callbacks:
        try:
            await cb(msg)
        except Exception as e:
            logger.debug(f"[event_bus] dead subscriber on {channel}: {e}")
            dead.append(cb)
    if dead:
        async with _lock:
            for cb in dead:
                try:
                    _subscribers[channel].remove(cb)
                except ValueError:
                    pass


async def subscribe_all(callback: Callable) -> None:
    """Subscribe to ALL channels (used by /ws/events multiplexer)."""
    for channel in ("detections", "log", "queue", "health", "windows", "vl"):
        await subscribe(channel, callback)


async def unsubscribe_all(callback: Callable) -> None:
    """Unsubscribe from all channels."""
    for channel in ("detections", "log", "queue", "health", "windows", "vl"):
        await unsubscribe(channel, callback)


def log_entry(kind: str, status: str, msg: str) -> dict:
    """Build a log entry dict (for publish to 'log' channel)."""
    return {
        "t": datetime.now().strftime("%H:%M:%S.") + f"{datetime.now().microsecond // 1000:03d}",
        "kind": kind,
        "status": status,
        "msg": msg,
    }
