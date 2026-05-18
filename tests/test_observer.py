"""Tests for observer module."""

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from observer import (
    ApprovalDecision,
    FRPHit,
    FRPLookup,
    FRPMiss,
    MeshObserver,
    MessageSent,
    PeerDiscovered,
    PeerLost,
    get_observer,
    observe,
    set_observer,
)


class TestEvents:
    def test_all_events_serialize(self):
        """All 7 event types serialize to dict without error."""
        import dataclasses

        events = [
            MessageSent(hwnd=1, text="hi"),
            ApprovalDecision(hwnd=2, tool="bash", decision="y"),
            PeerDiscovered(hwnd=3, title="Terminal"),
            PeerLost(hwnd=4, title="Terminal"),
            FRPLookup(error_text="err", env_class="dev"),
            FRPHit(fingerprint="abc", title="fix", confidence=0.9),
            FRPMiss(fingerprint="xyz"),
        ]
        for ev in events:
            d = dataclasses.asdict(ev)
            assert "ts" in d
            # Should be JSON-serializable
            json.dumps(d)


class TestMeshObserver:
    def test_file_backend_writes_jsonl(self, tmp_path):
        log_file = tmp_path / "events.jsonl"
        with MeshObserver(file=str(log_file)) as obs:
            obs.emit(MessageSent(hwnd=10, text="hello"))
            obs.emit(PeerDiscovered(hwnd=20, title="PowerShell"))

        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["text"] == "hello"

    def test_kind_field_present(self, tmp_path):
        log_file = tmp_path / "events.jsonl"
        with MeshObserver(file=str(log_file)) as obs:
            obs.emit(FRPMiss(fingerprint="abc"))

        line = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert line["kind"] == "FRPMiss"

    def test_console_no_crash(self):
        obs = MeshObserver(console=True)
        obs.emit(MessageSent(hwnd=1, text="test"))
        obs.close()

    def test_thread_safe_emit(self, tmp_path):
        log_file = tmp_path / "threaded.jsonl"
        obs = MeshObserver(file=str(log_file))
        threads = []
        for i in range(20):
            t = threading.Thread(target=obs.emit, args=(MessageSent(hwnd=i, text=f"msg{i}"),))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        obs.close()

        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 20

    def test_context_manager_closes_file(self, tmp_path):
        log_file = tmp_path / "ctx.jsonl"
        obs = MeshObserver(file=str(log_file))
        obs.__enter__()
        obs.emit(MessageSent(hwnd=1, text="x"))
        obs.__exit__(None, None, None)
        assert obs._file_handle is None


class TestObserveDecorator:
    def test_preserves_return_value(self):
        @observe
        def add(a, b):
            return a + b

        assert add(2, 3) == 5

    def test_with_factory_emits(self, tmp_path):
        log_file = tmp_path / "dec.jsonl"
        obs = MeshObserver(file=str(log_file))
        set_observer(obs)

        def factory(args, kwargs, result):
            return MessageSent(hwnd=0, text=str(result))

        @observe(event_factory=factory)
        def multiply(a, b):
            return a * b

        assert multiply(3, 4) == 12
        obs.close()

        line = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert line["text"] == "12"

        # Reset default observer
        set_observer(None)
