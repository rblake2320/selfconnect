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


class TestOtelOptionalImport:
    """OTel support is optional — observer must work with or without the package."""

    def test_otel_flag_false_when_not_installed(self):
        """When otel=True but opentelemetry is not installed, _otel must be False (no crash)."""
        import observer as obs_module

        was_available = obs_module._OTEL_AVAILABLE
        # Simulate OTel not being available
        obs_module._OTEL_AVAILABLE = False
        try:
            o = MeshObserver(otel=True)
            # _otel must be False because _OTEL_AVAILABLE is False
            assert o._otel is False
            # emit must not raise even though otel=True was requested
            o.emit(MessageSent(hwnd=1, text="no-crash"))
            o.close()
        finally:
            obs_module._OTEL_AVAILABLE = was_available

    def test_otel_flag_respected_when_available(self):
        """When _OTEL_AVAILABLE is True (or we simulate it), otel=True sets _otel=True."""
        import observer as obs_module

        was_available = obs_module._OTEL_AVAILABLE
        obs_module._OTEL_AVAILABLE = True
        try:
            o = MeshObserver(otel=True)
            assert o._otel is True
            o.close()
        finally:
            obs_module._OTEL_AVAILABLE = was_available

    def test_otel_false_by_default(self):
        """otel= defaults to False — no OTel emission even if package is present."""
        o = MeshObserver()
        assert o._otel is False
        o.close()

    def test_emit_with_otel_simulated(self, tmp_path):
        """Emit with otel=True using a mock tracer — must write file AND call span."""
        import unittest.mock as mock
        import observer as obs_module

        was_available = obs_module._OTEL_AVAILABLE
        obs_module._OTEL_AVAILABLE = True

        # Build a minimal mock tracer
        mock_span = mock.MagicMock()
        mock_span.__enter__ = mock.MagicMock(return_value=mock_span)
        mock_span.__exit__ = mock.MagicMock(return_value=False)
        mock_tracer = mock.MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span

        log_file = tmp_path / "otel.jsonl"
        try:
            with mock.patch("observer._otel_trace") as mock_otel_trace:
                mock_otel_trace.get_tracer.return_value = mock_tracer
                o = MeshObserver(file=str(log_file), otel=True)
                o.emit(FRPHit(fingerprint="abc", title="fix", confidence=0.95))
                o.close()

            # File backend also wrote the event
            line = json.loads(log_file.read_text(encoding="utf-8").strip())
            assert line["kind"] == "FRPHit"
            # OTel tracer was invoked
            mock_otel_trace.get_tracer.assert_called_once_with("selfconnect.observer")
        finally:
            obs_module._OTEL_AVAILABLE = was_available


class TestObserverSingleton:
    """Module-level singleton behavior."""

    def teardown_method(self):
        set_observer(None)

    def test_get_observer_creates_console_observer(self):
        set_observer(None)
        obs = get_observer()
        assert obs is not None
        assert obs._console is True

    def test_set_observer_replaces_singleton(self, tmp_path):
        log_file = tmp_path / "singleton.jsonl"
        custom = MeshObserver(file=str(log_file))
        set_observer(custom)
        assert get_observer() is custom
        custom.close()

    def test_set_observer_none_resets(self):
        set_observer(None)
        obs1 = get_observer()
        obs2 = get_observer()
        assert obs1 is obs2  # same object returned on subsequent calls
        obs1.close()
