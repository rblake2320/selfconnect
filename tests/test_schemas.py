"""
test_schemas.py — Unit tests for Pydantic schema validation.
"""
import pytest
from pydantic import ValidationError

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vision_server.models.schemas import (
    WindowInfo,
    Detection,
    VLDescription,
    QueueItem,
    LogEntry,
    MacroStep,
    HealthStatus,
    ActionRequest,
    CommandRequest,
    AttachRequest,
    WSMessage,
)


# ── WindowInfo ─────────────────────────────────────────────────────────────────

class TestWindowInfo:
    def test_valid(self):
        w = WindowInfo(hwnd="0x000A4F12", title="Notepad", exe="notepad.exe", active=False)
        assert w.hwnd == "0x000A4F12"

    def test_active_true(self):
        w = WindowInfo(hwnd="0x1", title="", exe="", active=True)
        assert w.active is True

    def test_missing_hwnd(self):
        with pytest.raises(ValidationError):
            WindowInfo(title="test", exe="test.exe", active=False)  # hwnd missing


# ── Detection ──────────────────────────────────────────────────────────────────

class TestDetection:
    def test_valid(self):
        d = Detection(id="d1", cls="button", label="OK", conf=0.92, x=0.1, y=0.2, w=0.05, h=0.03)
        assert d.cls == "button"
        assert d.conf == pytest.approx(0.92)

    def test_conf_float(self):
        d = Detection(id="d2", cls="text_field", label="Search", conf=1, x=0, y=0, w=1, h=1)
        assert isinstance(d.conf, float)

    def test_missing_field(self):
        with pytest.raises(ValidationError):
            Detection(id="d3", cls="button", label="OK", conf=0.5)  # x/y/w/h missing


# ── VLDescription ─────────────────────────────────────────────────────────────

class TestVLDescription:
    def test_valid(self):
        v = VLDescription(text="A button labeled Submit", tags=["button", "form"], tokens=42, ms=1200)
        assert v.tokens == 42
        assert "button" in v.tags

    def test_empty_tags(self):
        v = VLDescription(text="Empty screen", tags=[], tokens=0, ms=0)
        assert v.tags == []


# ── QueueItem ─────────────────────────────────────────────────────────────────

class TestQueueItem:
    def test_pending_state(self):
        q = QueueItem(id="q1", kind="click", target="OK", value="320,240", state="pending")
        assert q.state == "pending"

    def test_done_state(self):
        q = QueueItem(id="q2", kind="type", target="", value="hello world", state="done")
        assert q.state == "done"

    def test_all_kinds(self):
        for kind in ("click", "type", "key", "scroll", "wait"):
            q = QueueItem(id="x", kind=kind, target="", value="", state="pending")
            assert q.kind == kind


# ── LogEntry ──────────────────────────────────────────────────────────────────

class TestLogEntry:
    def test_valid(self):
        e = LogEntry(t="14:22:01.084", kind="capture", status="ok", msg="Frame captured")
        assert e.status == "ok"

    def test_fail_status(self):
        e = LogEntry(t="14:22:02.000", kind="vl", status="fail", msg="Ollama timeout")
        assert e.status == "fail"


# ── MacroStep ─────────────────────────────────────────────────────────────────

class TestMacroStep:
    def test_valid(self):
        s = MacroStep(t="+0.123s", action="click", target="button#submit", value="")
        assert s.t == "+0.123s"

    def test_type_action(self):
        s = MacroStep(t="+1.500s", action="type", target="", value="hello")
        assert s.value == "hello"


# ── HealthStatus ──────────────────────────────────────────────────────────────

class TestHealthStatus:
    def test_all_ok(self):
        h = HealthStatus(sdk="ok", yolo="degraded", vl="ok", claude="ok")
        assert h.sdk == "ok"
        assert h.yolo == "degraded"

    def test_all_down(self):
        h = HealthStatus(sdk="down", yolo="down", vl="down", claude="down")
        assert h.claude == "down"


# ── ActionRequest ─────────────────────────────────────────────────────────────

class TestActionRequest:
    def test_minimal_click(self):
        r = ActionRequest(kind="click")
        assert r.target == ""
        assert r.x is None

    def test_with_coords(self):
        r = ActionRequest(kind="click", x=0.5, y=0.3, hwnd=12345)
        assert r.x == pytest.approx(0.5)
        assert r.hwnd == 12345

    def test_type_action(self):
        r = ActionRequest(kind="type", value="hello world")
        assert r.value == "hello world"


# ── WSMessage ─────────────────────────────────────────────────────────────────

class TestWSMessage:
    def test_detections_channel(self):
        m = WSMessage(channel="detections", data=[])
        assert m.channel == "detections"

    def test_health_channel(self):
        m = WSMessage(channel="health", data={"sdk": "ok", "vl": "down"})
        assert m.data["sdk"] == "ok"

    def test_valid_channels(self):
        for ch in ("detections", "log", "queue", "health", "windows", "vl"):
            m = WSMessage(channel=ch, data={})
            assert m.channel == ch
