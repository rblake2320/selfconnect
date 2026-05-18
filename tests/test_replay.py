"""Tests for replay module."""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from replay import InjectionRecord, ReplaySession, SimulatedTerminal, load_log, replay_session


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
