"""Tests for sc_envelope wiring in hub_relay and spark2_client.

These are unit tests — no live hub, no Win32 calls.  We patch the HTTP
layer and verify that:
  1. hub_relay wraps outbound replies in signed envelopes
  2. hub_relay verifies inbound envelopes and rejects bad sigs
  3. hub_relay falls back to legacy plain text when no envelope present
  4. spark2_client wraps outbound CMDs in signed envelopes
  5. spark2_client extracts result from inbound signed envelope
  6. spark2_client rejects inbound envelope with bad sig
"""

import json
import sys
import types
from pathlib import Path

import pytest

# Make selfconnect importable without a full Win32 environment
SC_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SC_DIR))


# ── stub out self_connect so hub_relay can be imported headless ──────────────

def _make_win32_stub():
    mod = types.ModuleType("self_connect")

    class _FakeWin:
        hwnd = 1
        title = "test"
        exe_name = "test"
        pid = 0

    mod.list_windows = lambda: [_FakeWin()]
    mod.get_window_text = lambda hwnd: "text"
    mod.save_capture = lambda hwnd: "/tmp/cap.png"
    mod.send_frame = lambda *a, **k: None
    mod.send_string = lambda *a, **k: None
    return mod


sys.modules.setdefault("self_connect", _make_win32_stub())

import hub_relay  # noqa: E402
import spark2_client  # noqa: E402
from sc_envelope import Envelope, load_or_create_mesh_key  # noqa: E402


@pytest.fixture
def tmp_key(tmp_path, monkeypatch):
    key = load_or_create_mesh_key(tmp_path / "mesh.key")
    monkeypatch.setattr(hub_relay, "MESH_KEY", key)
    monkeypatch.setattr(spark2_client, "MESH_KEY", key)
    return key


# ─── hub_relay: reply_to_hub wraps in a signed envelope ─────────────────────

def test_reply_to_hub_sends_signed_envelope(tmp_key, monkeypatch):
    sent = []

    def fake_hub_post(path, payload, timeout=10):
        sent.append(payload)
        return {"ok": True}

    monkeypatch.setattr(hub_relay, "hub_post", fake_hub_post)
    hub_relay.reply_to_hub("SOME RESULT", conversation_id="conv-1")

    assert len(sent) == 1
    content = sent[0]["content"]
    env = Envelope.from_json(content)
    assert env.verify(tmp_key)
    assert env.kind == "reply"
    assert env.payload["result"] == "SOME RESULT"
    assert env.sender == "windows-a"


# ─── hub_relay: process_messages verifies envelope and routes cmd ────────────

def test_process_messages_accepts_valid_envelope(tmp_key, monkeypatch):
    env = Envelope(
        sender="cc-spark2", recipient="windows-a",
        kind="cmd", payload={"cmd": "CMD:MESH_STATUS"},
    ).sign(tmp_key)

    results = []
    monkeypatch.setattr(hub_relay, "reply_to_hub", lambda r, conv_id=None: results.append(r))
    monkeypatch.setattr(hub_relay, "execute_cmd", lambda content, conv_id=None: f"RESULT:{content}")

    msgs = [{"from_agent": "cc-spark2", "content": env.to_json(), "conversation_id": None}]
    hub_relay.process_messages(msgs)
    assert results and results[0].startswith("RESULT:CMD:MESH_STATUS")


def test_process_messages_drops_bad_sig(tmp_key, monkeypatch, tmp_path):
    other_key = load_or_create_mesh_key(tmp_path / "other.key")
    env = Envelope(
        sender="cc-spark2", recipient="windows-a",
        kind="cmd", payload={"cmd": "CMD:MESH_STATUS"},
    ).sign(other_key)  # signed with WRONG key

    results = []
    monkeypatch.setattr(hub_relay, "reply_to_hub", lambda r, conv_id=None: results.append(r))

    msgs = [{"from_agent": "cc-spark2", "content": env.to_json(), "conversation_id": None}]
    hub_relay.process_messages(msgs)
    assert not results  # bad sig → dropped, no reply


def test_process_messages_accepts_legacy_plain_text(tmp_key, monkeypatch):
    results = []
    monkeypatch.setattr(hub_relay, "reply_to_hub", lambda r, conv_id=None: results.append(r))
    monkeypatch.setattr(hub_relay, "execute_cmd", lambda content, conv_id=None: f"LEGACY:{content}")

    msgs = [{"from_agent": "cc-spark2", "content": "CMD:LIST_WINDOWS", "conversation_id": None}]
    hub_relay.process_messages(msgs)
    assert results and "LEGACY:" in results[0]


# ─── spark2_client: send wraps cmd in signed envelope ────────────────────────

def test_hub_transport_send_wraps_envelope(tmp_key, monkeypatch):
    sent = []

    class FakeResp:
        def read(self):
            return json.dumps({"ok": True}).encode()

    def fake_urlopen(req, timeout=10):
        sent.append(json.loads(req.data))
        return FakeResp()

    monkeypatch.setattr(spark2_client.urllib.request, "urlopen", fake_urlopen)
    transport = spark2_client.HubTransport()
    transport.send("CMD:MESH_STATUS")

    assert sent
    content = sent[0]["content"]
    env = Envelope.from_json(content)
    assert env.verify(tmp_key)
    assert env.kind == "cmd"
    assert env.payload["cmd"] == "CMD:MESH_STATUS"


# ─── spark2_client._call: extracts result from signed reply ─────────────────

def _make_envelope_reply(key, tag, result="MESH_OK"):
    env = Envelope(
        sender="windows-a", recipient="cc-spark2",
        kind="reply", payload={"result": f"{result}\nTAG:{tag}"},
    ).sign(key)
    return env.to_json()


def test_sc_call_extracts_signed_reply(tmp_key, monkeypatch):
    tag_holder = []

    call_count = [0]

    class FakeResp:
        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

    def fake_urlopen(req, timeout=10):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: send — capture tag from envelope payload
            body = json.loads(req.data)
            env = Envelope.from_json(body["content"])
            tag_holder.append(env.payload["cmd"].split("TAG:")[1].strip())
            return FakeResp(json.dumps({"ok": True}).encode())
        # Second call: poll inbox — return signed reply
        tag = tag_holder[0] if tag_holder else "RPC-00000000"
        reply_content = _make_envelope_reply(tmp_key, tag)
        msgs = {"messages": [{"from_agent": "windows-a", "content": reply_content}]}
        return FakeResp(json.dumps(msgs).encode())

    monkeypatch.setattr(spark2_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(spark2_client, "POLL_INTERVAL", 0)

    sc = spark2_client.SC()
    result = sc._call("MESH_STATUS")
    assert "MESH_OK" in result


def test_sc_call_rejects_bad_sig(tmp_key, monkeypatch, tmp_path):
    other_key = load_or_create_mesh_key(tmp_path / "bad.key")
    call_count = [0]

    class FakeResp:
        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

    def fake_urlopen(req, timeout=10):
        call_count[0] += 1
        if call_count[0] == 1:
            return FakeResp(json.dumps({"ok": True}).encode())
        # Return reply signed with wrong key — should be rejected
        reply_content = _make_envelope_reply(other_key, "RPC-deadbeef")
        msgs = {"messages": [{"from_agent": "windows-a", "content": reply_content}]}
        return FakeResp(json.dumps(msgs).encode())

    monkeypatch.setattr(spark2_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(spark2_client, "POLL_INTERVAL", 0)
    monkeypatch.setattr(spark2_client, "REPLY_TIMEOUT", 0.1)  # short timeout

    sc = spark2_client.SC(timeout=0.1)
    with pytest.raises(TimeoutError):
        sc._call("MESH_STATUS")  # bad sig → skipped → timeout


# ─── replay protection: stale envelopes are dropped ─────────────────────────

def test_process_messages_drops_stale_envelope(tmp_key, monkeypatch):
    """hub_relay must reject a validly-signed but replayed (old) envelope."""
    import time as _time
    env = Envelope(
        sender="cc-spark2", recipient="windows-a",
        kind="cmd", payload={"cmd": "CMD:MESH_STATUS"},
    )
    env.ts = _time.time() - 400  # older than ENVELOPE_MAX_AGE_S (300s)
    env.sign(tmp_key)

    results = []
    monkeypatch.setattr(hub_relay, "reply_to_hub", lambda r, conv_id=None: results.append(r))

    msgs = [{"from_agent": "cc-spark2", "content": env.to_json(), "conversation_id": None}]
    hub_relay.process_messages(msgs)
    assert not results  # stale → dropped, no reply


def test_sc_call_drops_stale_reply(tmp_key, monkeypatch, tmp_path):
    """spark2_client must reject a validly-signed but replayed (old) reply."""
    import time as _time
    call_count = [0]

    class FakeResp:
        def __init__(self, data):
            self._data = data
        def read(self):
            return self._data

    def fake_urlopen(req, timeout=10):
        call_count[0] += 1
        if call_count[0] == 1:
            return FakeResp(json.dumps({"ok": True}).encode())
        # Reply signed with correct key but timestamp 400s in the past
        env = Envelope(
            sender="windows-a", recipient="cc-spark2",
            kind="reply", payload={"result": "MESH_OK\nTAG:RPC-deadbeef"},
        )
        env.ts = _time.time() - 400
        env.sign(tmp_key)
        msgs = {"messages": [{"from_agent": "windows-a", "content": env.to_json()}]}
        return FakeResp(json.dumps(msgs).encode())

    monkeypatch.setattr(spark2_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(spark2_client, "POLL_INTERVAL", 0)
    monkeypatch.setattr(spark2_client, "REPLY_TIMEOUT", 0.2)

    sc = spark2_client.SC(timeout=0.2)
    with pytest.raises(TimeoutError):
        sc._call("MESH_STATUS")  # stale reply → skipped → timeout
