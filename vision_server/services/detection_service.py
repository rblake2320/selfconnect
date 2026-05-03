"""
detection_service.py — UI element detection.
ASSIGNED TO: Agent C

Strategy:
  1. For native Win32 apps: list_child_controls(hwnd) → real Win32 control rects
  2. For browser/Electron windows (single rendering surface): llava structured prompt
  3. Results pushed to event bus "detections" channel after each run

Interface (do not change):
  def get_latest_detections() -> list[Detection]
  async def detect(hwnd: int, frame_jpeg: bytes | None = None) -> list[Detection]
  async def start_detection_loop() -> None
  async def stop_detection_loop() -> None

Detection frequency: every 2s (not every frame — detection is slower than capture)

Win32 detection:
  from self_connect import list_child_controls
  controls = list_child_controls(hwnd)  # returns list of (hwnd, class_name, rect, text)
  Map class_name to cls: "Button" -> "button", "Edit" -> "text_field", "Static" -> "label"

llava structured prompt for browsers:
  "List all visible interactive elements. For each: type (button/input/link), label text,
   position as x,y,w,h as 0..1 fractions of image size. Reply as JSON array only."
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

_latest: list = []
_task: asyncio.Task | None = None


def get_latest_detections() -> list:
    return list(_latest)


async def detect(hwnd: int, frame_jpeg: bytes | None = None) -> list:
    """
    Run detection on hwnd. Returns list of Detection dicts.
    Tries Win32 child controls first; falls back to llava if browser window.
    Agent C: implement this.
    """
    global _latest
    from concurrent.futures import ThreadPoolExecutor

    results = []

    # --- Strategy 1: Win32 child controls ---
    try:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as pool:
            controls = await loop.run_in_executor(pool, _get_win32_controls, hwnd)

        if controls:
            results = controls
            _latest = results
            from vision_server.services.event_bus import publish
            await publish("detections", [d.model_dump() for d in results])
            return results
    except Exception as e:
        logger.debug(f"[detect] Win32 failed: {e}")

    # --- Strategy 2: llava for browser/Electron/terminal rendering surfaces ---
    if frame_jpeg is None:
        try:
            from vision_server.services.capture_service import _capture_one
            loop = asyncio.get_event_loop()
            frame_jpeg = await loop.run_in_executor(None, _capture_one, hwnd)
        except Exception as e:
            logger.debug(f"[detect] capture fallback failed: {e}")

    if frame_jpeg:
        try:
            results = await _detect_via_llava(frame_jpeg)
        except Exception as e:
            logger.debug(f"[detect] llava failed: {e}")

    _latest = results
    from vision_server.services.event_bus import publish
    await publish("detections", [d.model_dump() if hasattr(d, 'model_dump') else d for d in results])
    return results


def _get_win32_controls(hwnd: int) -> list:
    """Use list_child_controls to get Win32 UI elements."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

    from vision_server import config
    from vision_server.models.schemas import Detection
    from self_connect import list_child_controls
    import ctypes, ctypes.wintypes

    # Get parent window rect for normalization
    user32 = ctypes.windll.user32
    prect = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(prect))
    pw = max(prect.right - prect.left, 1)
    ph = max(prect.bottom - prect.top, 1)

    results = []
    controls = list_child_controls(hwnd)

    CLASS_MAP = {
        "button": "button",
        "edit": "text_field",
        "static": "label",
        "combobox": "text_field",
        "listbox": "label",
        "richedit": "text_field",
        "inputsite": "text_field",
        "desktopwindowcontentsource": "image",
        "desktopwindowcontentbridge": "image",
    }

    for control in controls[:50]:  # cap at 50
        try:
            child = int(control.get("hwnd", 0))
            cls_raw = str(control.get("class_name", ""))
            cls_name = cls_raw.lower()

            mapped = None
            for key, val in CLASS_MAP.items():
                if key in cls_name:
                    mapped = val
                    break
            if not mapped:
                continue

            left, top, w, h = control.get("rect", (0, 0, 0, 0))

            # Skip invisible/zero-size
            if not user32.IsWindowVisible(child):
                continue
            if w < 5 or h < 5:
                continue

            label = str(control.get("text", "")).strip() or cls_raw or f"hwnd {child}"

            # Normalize to 0..1 relative to parent
            x = (left - prect.left) / pw
            y = (top - prect.top) / ph
            nw = w / pw
            nh = h / ph

            results.append(Detection(
                id=f"d{child}",
                cls=mapped,
                label=label[:40],
                conf=config.DETECTION_WIN32_CONFIDENCE,
                x=round(_clamp01(x), 4),
                y=round(_clamp01(y), 4),
                w=round(_clamp01(nw), 4),
                h=round(_clamp01(nh), 4),
            ))
        except Exception:
            continue

    return results


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


async def _detect_via_llava(frame_jpeg: bytes) -> list:
    """Use Ollama llava to detect UI elements in a browser screenshot."""
    import base64, json, httpx
    from vision_server.config import OLLAMA_URL, OLLAMA_VL_MODEL, OLLAMA_VL_TIMEOUT
    from vision_server.models.schemas import Detection

    b64 = base64.b64encode(frame_jpeg).decode()
    prompt = (
        'List all visible interactive UI elements (buttons, inputs, links, labels). '
        'For each: {"type":"button|input|link|label", "label":"text", "x":0.0,"y":0.0,"w":0.0,"h":0.0} '
        'where x,y,w,h are 0..1 fractions of image size. Reply as a JSON array only, no other text.'
    )

    async with httpx.AsyncClient(timeout=OLLAMA_VL_TIMEOUT) as client:
        r = await client.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": OLLAMA_VL_MODEL, "prompt": prompt, "images": [b64], "stream": False},
        )
    r.raise_for_status()
    text = r.json().get("response", "[]")

    # Parse JSON from response
    start = text.find("[")
    end = text.rfind("]") + 1
    if start == -1 or end == 0:
        return []

    items = json.loads(text[start:end])
    TYPE_MAP = {"button": "button", "input": "text_field", "link": "button", "label": "label"}

    results = []
    for i, item in enumerate(items[:20]):
        try:
            x = _clamp01(float(item.get("x", 0)))
            y = _clamp01(float(item.get("y", 0)))
            w = _clamp01(float(item.get("w", 0.1)))
            h = _clamp01(float(item.get("h", 0.05)))
        except (TypeError, ValueError):
            continue
        results.append(Detection(
            id=f"vl_{i}",
            cls=TYPE_MAP.get(item.get("type", "label"), "label"),
            label=item.get("label", "")[:40],
            conf=config.DETECTION_VL_CONFIDENCE,
            x=x,
            y=y,
            w=w,
            h=h,
        ))
    return results


async def start_detection_loop():
    global _task

    async def _loop():
        from vision_server import config
        from vision_server.services.capture_service import get_latest_frame
        while True:
            hwnd = config.active_hwnd
            if hwnd:
                frame = get_latest_frame()
                try:
                    await detect(hwnd, frame)
                except Exception as e:
                    logger.debug(f"[detect] loop error: {e}")
            await asyncio.sleep(2)  # detect every 2s

    _task = asyncio.create_task(_loop())


async def stop_detection_loop():
    global _task
    if _task:
        _task.cancel()
