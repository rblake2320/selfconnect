"""
capture_service.py — Background capture loop.
ASSIGNED TO: Agent B

Responsibilities:
  - Maintain a background thread that captures the active window at ~CAPTURE_FPS
  - Try PrintWindow (SDK capture_window) first; fall back to PIL.ImageGrab if all-black
  - Encode each frame as JPEG (binary) and broadcast to /ws/capture WebSocket clients
  - Expose: start(), stop(), set_active_hwnd(hwnd), get_latest_frame() -> bytes | None

Interface (do not change signatures — routers depend on these):
  async def start() -> None
  async def stop() -> None
  def set_active_hwnd(hwnd: int) -> None
  def get_latest_frame() -> bytes | None          # latest JPEG bytes
  def add_frame_subscriber(cb: Callable) -> None  # cb(frame_bytes: bytes)
  def remove_frame_subscriber(cb: Callable) -> None
"""
import asyncio
import io
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

logger = logging.getLogger(__name__)

_active_hwnd: int = 0
_running = False
_thread: threading.Thread | None = None
_latest_frame: bytes | None = None
_subscribers: list[Callable] = []
_sub_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None


def set_active_hwnd(hwnd: int) -> None:
    global _active_hwnd
    _active_hwnd = hwnd
    logger.info(f"[capture] active hwnd = {hwnd}")


def get_latest_frame() -> bytes | None:
    return _latest_frame


def add_frame_subscriber(cb: Callable) -> None:
    with _sub_lock:
        _subscribers.append(cb)


def remove_frame_subscriber(cb: Callable) -> None:
    with _sub_lock:
        try:
            _subscribers.remove(cb)
        except ValueError:
            pass


def _capture_one(hwnd: int) -> bytes | None:
    """
    Capture a single frame from hwnd. Returns JPEG bytes or None on failure.
    Strategy:
      1. Try SDK capture_window() (PrintWindow)
      2. If result is all-black (GPU compositing), fall back to PIL.ImageGrab
    """
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

    from vision_server import config

    # --- Strategy 1: PrintWindow via SDK ---
    try:
        from self_connect import capture_window
        img = capture_window(hwnd)
        if img is not None:
            import numpy as np
            arr = np.array(img)
            if arr.max() > 10:  # not all-black
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=config.CAPTURE_QUALITY)
                return buf.getvalue()
    except Exception as e:
        logger.debug(f"[capture] PrintWindow failed: {e}")

    # --- Strategy 2: PIL.ImageGrab (for GPU-composited windows like Chrome) ---
    try:
        import ctypes
        import ctypes.wintypes
        from PIL import ImageGrab

        user32 = ctypes.windll.user32
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return None

        img = ImageGrab.grab(
            bbox=(rect.left, rect.top, rect.right, rect.bottom),
            all_screens=True,
        )
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=config.CAPTURE_QUALITY)
        return buf.getvalue()
    except Exception as e:
        logger.debug(f"[capture] ImageGrab failed: {e}")

    return None


def _capture_loop():
    global _latest_frame, _running
    from vision_server import config

    logger.info("[capture] loop started")
    while _running:
        t0 = time.monotonic()

        hwnd = _active_hwnd
        if hwnd:
            frame = _capture_one(hwnd)
            if frame:
                _latest_frame = frame
                # Notify subscribers (WebSocket handlers)
                with _sub_lock:
                    cbs = list(_subscribers)
                for cb in cbs:
                    try:
                        if _loop and _loop.is_running():
                            asyncio.run_coroutine_threadsafe(cb(frame), _loop)
                    except Exception as e:
                        logger.debug(f"[capture] subscriber error: {e}")

        elapsed = time.monotonic() - t0
        sleep = max(0, (1.0 / config.CAPTURE_FPS) - elapsed)
        time.sleep(sleep)

    logger.info("[capture] loop stopped")


async def start() -> None:
    global _running, _thread, _loop
    if _running:
        return
    _loop = asyncio.get_event_loop()
    _running = True
    _thread = threading.Thread(target=_capture_loop, daemon=True, name="capture-loop")
    _thread.start()
    logger.info("[capture] service started")


async def stop() -> None:
    global _running
    _running = False
    if _thread:
        _thread.join(timeout=3)
    logger.info("[capture] service stopped")
