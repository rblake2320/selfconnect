"""Tests for replay module."""

import contextlib
import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from replay import InjectionRecord, ReplaySession, SimulatedTerminal, load_log, record_session, replay_session


class TestSimulatedTerminal:
    def test_records_injections(self):
        term = SimulatedTerminal(hwnd=42)
        term.send_string("hello")
        term.send_string("world", submitted=True)
        log = term.get_log()
        assert len(log) == 2
        assert log[0].text == "hello"
        assert log[1].submitted is True
        assert len(term) == 2

    def test_thread_safety(self):
        term = SimulatedTerminal(hwnd=1)
        threads = []
        for i in range(50):
            t = threading.Thread(target=term.send_string, args=(f"msg{i}",))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        assert len(term) == 50

    def test_dump_load_roundtrip(self, tmp_path):
        term = SimulatedTerminal(hwnd=99)
        term.send_string("alpha")
        term.send_string("beta", submitted=True)
        out_file = tmp_path / "session.jsonl"
        count = term.dump_jsonl(out_file)
        assert count == 2

        records = load_log(out_file)
        assert len(records) == 2
        assert records[0].text == "alpha"
        assert records[1].submitted is True
        assert records[1].hwnd == 99


class TestReplaySession:
    def test_replays_all_records(self):
        now = time.time()
        records = [
            InjectionRecord(ts=now, hwnd=1, text="a"),
            InjectionRecord(ts=now + 0.01, hwnd=1, text="b"),
            InjectionRecord(ts=now + 0.02, hwnd=1, text="c"),
        ]
        term = SimulatedTerminal(hwnd=1)
        session = ReplaySession(records, speed=0)
        result = session.replay(terminal=term)
        assert len(result) == 3
        assert len(term) == 3

    def test_speed_zero_no_delays(self):
        now = time.time()
        records = [
            InjectionRecord(ts=now, hwnd=1, text="x"),
            InjectionRecord(ts=now + 10.0, hwnd=1, text="y"),  # 10 sec gap
        ]
        term = SimulatedTerminal(hwnd=1)
        session = ReplaySession(records, speed=0)
        start = time.time()
        session.replay(terminal=term)
        elapsed = time.time() - start
        assert elapsed < 1.0  # Should be nearly instant

    def test_duration_property(self):
        records = [
            InjectionRecord(ts=100.0, hwnd=1, text="a"),
            InjectionRecord(ts=105.5, hwnd=1, text="b"),
        ]
        session = ReplaySession(records)
        assert session.duration == pytest.approx(5.5)

    def test_replay_session_function(self, tmp_path):
        now = time.time()
        log_file = tmp_path / "test.jsonl"
        records = [
            {"ts": now, "hwnd": 1, "text": "hello", "submitted": False},
            {"ts": now + 0.01, "hwnd": 1, "text": "world", "submitted": True},
        ]
        with open(log_file, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        result = replay_session(log_file, speed=0)
        assert len(result) == 2
        assert result[0].text == "hello"


class TestRecordSession:
    """Tests for the record_session context manager and monkey-patch restore."""

    def _make_fake_self_connect(self):
        """Create a minimal fake self_connect module with a tracked send_string."""
        mod = types.ModuleType("self_connect")
        calls = []

        def fake_send(target, text, *args, **kwargs):
            calls.append(text)

        mod.send_string = fake_send
        mod._calls = calls
        return mod

    def test_record_session_patches_and_restores(self, tmp_path):
        """
        record_session must:
        1. Monkey-patch self_connect.send_string while inside the context.
        2. Restore the original function after the context exits (even on exception).
        3. Write JSONL to the output file.
        """
        import sys

        sc = self._make_fake_self_connect()
        original_send = sc.send_string
        sys.modules["self_connect"] = sc

        output = tmp_path / "recorded.jsonl"
        try:
            with record_session(hwnd=99, output_file=output) as log:
                # Inside: send_string must be the patched version
                assert sc.send_string is not original_send, "send_string was not patched"
                sc.send_string(99, "msg_one")
                sc.send_string(99, "msg_two")
            # Outside: send_string must be restored
            assert sc.send_string is original_send, "send_string was not restored after normal exit"

            # Log in-memory
            assert len(log) == 2
            assert log[0].text == "msg_one"
            assert log[1].text == "msg_two"

            # JSONL on disk
            lines = output.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 2
            assert json.loads(lines[0])["text"] == "msg_one"
            assert json.loads(lines[1])["text"] == "msg_two"
        finally:
            sys.modules.pop("self_connect", None)

    def test_record_session_restores_on_exception(self, tmp_path):
        """send_string must be restored even when an exception is raised inside the context."""
        import sys

        sc = self._make_fake_self_connect()
        original_send = sc.send_string
        sys.modules["self_connect"] = sc

        output = tmp_path / "exc_record.jsonl"
        try:
            with pytest.raises(RuntimeError, match="deliberate"):
                with record_session(hwnd=1, output_file=output) as log:
                    sc.send_string(1, "before_crash")
                    raise RuntimeError("deliberate")

            # After exception: send_string must be the original
            assert sc.send_string is original_send, "send_string not restored after exception"
        finally:
            sys.modules.pop("self_connect", None)

    def test_record_session_without_self_connect_yields_empty(self, tmp_path):
        """If self_connect is not importable, record_session must yield an empty list without crashing."""
        import sys

        # Ensure self_connect is NOT in sys.modules
        sys.modules.pop("self_connect", None)
        # Block import of self_connect by placing a failing entry
        sys.modules["self_connect"] = None  # None triggers ImportError on 'import self_connect'

        output = tmp_path / "no_sc.jsonl"
        try:
            with record_session(hwnd=0, output_file=output) as log:
                pass
            assert log == []
        finally:
            sys.modules.pop("self_connect", None)


class TestReplayDryRun:
    def test_replay_dry_returns_all_records(self):
        now = time.time()
        records = [
            InjectionRecord(ts=now, hwnd=1, text="x"),
            InjectionRecord(ts=now + 1.0, hwnd=1, text="y"),
        ]
        session = ReplaySession(records, speed=0)
        dry = session.replay_dry()
        assert len(dry) == 2
        assert dry[0].text == "x"

    def test_duration_single_record(self):
        records = [InjectionRecord(ts=50.0, hwnd=1, text="only")]
        session = ReplaySession(records)
        assert session.duration == 0.0

    def test_duration_empty(self):
        session = ReplaySession([])
        assert session.duration == 0.0

    def test_clear_resets_simulated_terminal(self):
        term = SimulatedTerminal(hwnd=7)
        term.send_string("a")
        term.send_string("b")
        assert len(term) == 2
        term.clear()
        assert len(term) == 0
