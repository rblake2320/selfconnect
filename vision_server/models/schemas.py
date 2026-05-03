"""
schemas.py — Pydantic models matching vision_agent_dashboard.html mock data shapes exactly.
All field names match what the dashboard expects.
"""
from pydantic import BaseModel
from typing import Optional


class WindowInfo(BaseModel):
    hwnd: str        # hex string e.g. "0x000A4F12"
    title: str
    exe: str
    active: bool


class Detection(BaseModel):
    id: str
    cls: str         # "button", "text_field", "label", "image"
    label: str
    conf: float      # 0..1
    x: float         # 0..1 relative to capture width
    y: float
    w: float
    h: float


class VLDescription(BaseModel):
    text: str
    tags: list[str]
    tokens: int
    ms: int


class QueueItem(BaseModel):
    id: str
    kind: str        # "click", "type", "key", "scroll", "wait"
    target: str
    value: str
    state: str       # "pending", "running", "done", "failed"


class LogEntry(BaseModel):
    t: str           # timestamp string e.g. "14:22:01.084"
    kind: str        # "capture", "yolo", "vl", "click", "type", "system"
    status: str      # "ok", "fail", "pending"
    msg: str


class MacroStep(BaseModel):
    t: str           # relative time e.g. "+0.000s"
    action: str      # "capture", "click", "type", "key"
    target: str
    value: str


class HealthStatus(BaseModel):
    sdk: str         # "ok", "degraded", "down"
    yolo: str        # "ok", "degraded", "down" (always "degraded" in v1 — no custom model)
    vl: str          # "ok", "degraded", "down"
    claude: str      # "ok", "degraded", "down"


class ActionRequest(BaseModel):
    kind: str        # "click", "type", "key", "scroll", "wait"
    target: str = ""
    value: str = ""
    x: Optional[float] = None
    y: Optional[float] = None
    hwnd: Optional[int] = None


class CommandRequest(BaseModel):
    text: str        # natural language command


class AttachRequest(BaseModel):
    hwnd: int


class WSMessage(BaseModel):
    """Wrapper for multiplexed /ws/events messages."""
    channel: str     # "detections", "log", "queue", "health", "windows", "vl"
    data: object
