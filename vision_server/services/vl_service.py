"""
vl_service.py — Vision-Language description via Ollama llava.
ASSIGNED TO: Agent B

Tasks:
  1. Implement describe_current_frame() — call Ollama llava with the latest capture frame
  2. Parse response into VLDescription(text, tags, tokens, ms)
  3. Publish result to event bus channel "vl" after each call

Interface (do not change):
  async def describe_current_frame() -> VLDescription
  async def describe_frame(jpeg_bytes: bytes) -> VLDescription

Ollama API:
  POST http://localhost:11434/api/generate
  { "model": "llava:13b", "prompt": "...", "images": ["<base64>"], "stream": false }

Prompts to use:
  General description: "Describe what you see on this screen in detail. What application is shown?"
  Element listing: "List all interactive UI elements (buttons, inputs, links) visible. For each give: type, label, approximate position as percentage of screen width/height."
"""
import base64
import logging
import time

logger = logging.getLogger(__name__)


async def describe_current_frame():
    """Describe the current capture frame using Ollama llava."""
    from vision_server.services.capture_service import get_latest_frame
    frame = get_latest_frame()
    if not frame:
        from vision_server.models.schemas import VLDescription
        return VLDescription(text="No frame available", tags=[], tokens=0, ms=0)
    return await describe_frame(frame)


async def describe_frame(jpeg_bytes: bytes):
    """
    Call Ollama llava with the given JPEG bytes.
    Returns VLDescription.
    Agent B: implement this function.
    """
    from vision_server.models.schemas import VLDescription
    from vision_server.config import OLLAMA_URL, OLLAMA_VL_MODEL, OLLAMA_VL_TIMEOUT
    import httpx

    b64 = base64.b64encode(jpeg_bytes).decode()
    prompt = "Describe what you see on this screen. What application is shown? What are the key UI elements?"

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_VL_TIMEOUT) as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_VL_MODEL,
                    "prompt": prompt,
                    "images": [b64],
                    "stream": False,
                },
            )
        r.raise_for_status()
        data = r.json()
        text = data.get("response", "")
        tokens = data.get("eval_count", 0)
        ms = int((time.monotonic() - t0) * 1000)

        # Extract simple semantic tags from response
        tags = _extract_tags(text)

        result = VLDescription(text=text, tags=tags, tokens=tokens, ms=ms)

        # Publish to event bus
        from vision_server.services.event_bus import publish
        await publish("vl", result.model_dump())

        return result

    except Exception as e:
        logger.error(f"[vl] Ollama error: {e}")
        ms = int((time.monotonic() - t0) * 1000)
        return VLDescription(
            text=f"VL inference failed: {e}",
            tags=["error"],
            tokens=0,
            ms=ms,
        )


def _extract_tags(text: str) -> list[str]:
    """Simple keyword extraction for semantic tags."""
    keywords = {
        "login": ["login", "sign in", "sign-in", "username", "password"],
        "form": ["form", "input", "field", "submit"],
        "browser": ["browser", "chrome", "edge", "firefox", "url", "address bar"],
        "terminal": ["terminal", "command", "prompt", "console", "powershell", "bash"],
        "dashboard": ["dashboard", "chart", "graph", "metric", "monitor"],
        "code": ["code", "editor", "ide", "file", "function", "class"],
        "dialog": ["dialog", "popup", "modal", "confirm", "cancel", "ok"],
    }
    text_lower = text.lower()
    return [tag for tag, words in keywords.items() if any(w in text_lower for w in words)]
