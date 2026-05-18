"""
Session recording and replay harness for SelfConnect SDK.

Records injections to JSONL format and replays them against real or simulated terminals.

Usage:
    from replay import SimulatedTerminal, ReplaySession, load_log

    terminal = SimulatedTerminal(hwnd=99)
    terminal.send_string("hello")
    terminal.dump_jsonl("session.jsonl")

    records = load_log("session.jsonl")
    session = ReplaySession(records, speed=0)
    session.replay(terminal=terminal)
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "InjectionRecord",
    "ReplaySession",
    "SimulatedTerminal",
    "load_log",
    "record_session",
    "replay_session",
]


@dataclass
class InjectionRecord:
    """A single recorded injection event."""

    ts: float
    hwnd: int
    text: str
    submitted: bool = False


class SimulatedTerminal:
    """In-memory terminal that records injections for testing."""

    def __init__(self, hwnd: int = 0) -> None:
        self.hwnd = hwnd
        self._lock = threading.Lock()
        self._log: list[InjectionRecord] = []

    def send_string(self, text: str, submitted: bool = False) -> None:
        """Record an injection."""
        record = InjectionRecord(ts=time.time(), hwnd=self.hwnd, text=text, submitted=submitted)
        with self._lock:
            self._log.append(record)

    def get_log(self) -> list[InjectionRecord]:
        """Return a copy of all recorded injections."""
        with self._lock:
            return list(self._log)

    def clear(self) -> None:
        """Clear all recorded injections."""
        with self._lock:
            self._log.clear()

    def dump_jsonl(self, path: str | Path) -> int:
        """Write all records to a JSONL file. Returns number of records written."""
        records = self.get_log()
        p = Path(path)
        with open(p, "w", encoding="utf-8") as f:
            for rec in records:
                line = json.dumps({
                    "ts": rec.ts,
                    "hwnd": rec.hwnd,
                    "text": rec.text,
                    "submitted": rec.submitted,
                })
                f.write(line + "\n")
        return len(records)

    def __len__(self) -> int:
        with self._lock:
            return len(self._log)

    def __repr__(self) -> str:
        return f"SimulatedTerminal(hwnd={self.hwnd}, records={len(self)})"


class ReplaySession:
    """Replays a list of injection records against a terminal."""

    def __init__(self, records: list[InjectionRecord], speed: float = 1.0) -> None:
        self._records = list(records)
        self._speed = speed

    def replay(
        self,
        terminal: SimulatedTerminal | None = None,
        hwnd: int | None = None,
    ) -> list[InjectionRecord]:
        """
        Replay all records.

        If hwnd is given, uses real self_connect.send_string.
        If terminal is given, uses terminal.send_string.
        Returns the list of replayed records.
        """
        replayed: list[InjectionRecord] = []

        real_send = None
        if hwnd is not None:
            try:
                import self_connect
                real_send = self_connect.send_string
            except ImportError:
                pass

        prev_ts: float | None = None
        for rec in self._records:
            # Delay between records
            if prev_ts is not None and self._speed > 0:
                delta = (rec.ts - prev_ts) / self._speed
                if delta > 0:
                    time.sleep(delta)
            prev_ts = rec.ts

            # Send
            if hwnd is not None and real_send is not None:
                real_send(hwnd, rec.text)
            elif terminal is not None:
                terminal.send_string(rec.text, submitted=rec.submitted)

            replayed.append(rec)

        return replayed

    def replay_dry(self) -> list[InjectionRecord]:
        """Return all records without sending anything."""
        return list(self._records)

    @property
    def duration(self) -> float:
        """Total duration of the session in seconds."""
        if len(self._records) < 2:
            return 0.0
        return self._records[-1].ts - self._records[0].ts


def load_log(path: str | Path) -> list[InjectionRecord]:
    """Load injection records from a JSONL file."""
    records: list[InjectionRecord] = []
    p = Path(path)
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            records.append(InjectionRecord(
                ts=d["ts"],
                hwnd=d["hwnd"],
                text=d["text"],
                submitted=d.get("submitted", False),
            ))
    return records


@contextlib.contextmanager
def record_session(hwnd: int, output_file: str | Path) -> Generator[list[InjectionRecord], None, None]:
    """
    Context manager that monkey-patches self_connect.send_string to record injections.

    Usage:
        with record_session(hwnd, "session.jsonl") as log:
            self_connect.send_string(hwnd, "hello")
        # log contains all InjectionRecord items; file is written on exit
    """
    records: list[InjectionRecord] = []

    try:
        import self_connect
    except ImportError:
        # If self_connect is unavailable, yield empty and return
        yield records
        return

    original_send = self_connect.send_string

    def patched_send(target: Any, text: str, *args: Any, **kwargs: Any) -> Any:
        rec = InjectionRecord(ts=time.time(), hwnd=hwnd, text=text, submitted=False)
        records.append(rec)
        return original_send(target, text, *args, **kwargs)

    self_connect.send_string = patched_send
    try:
        yield records
    finally:
        self_connect.send_string = original_send
        # Write JSONL
        p = Path(output_file)
        with open(p, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps({
                    "ts": rec.ts,
                    "hwnd": rec.hwnd,
                    "text": rec.text,
                    "submitted": rec.submitted,
                }) + "\n")


def replay_session(
    log_file: str | Path,
    target_hwnd: int | None = None,
    speed: float = 1.0,
) -> list[InjectionRecord]:
    """Load a log file and replay it. Returns replayed records."""
    records = load_log(log_file)
    session = ReplaySession(records, speed=speed)
    if target_hwnd is not None:
        return session.replay(hwnd=target_hwnd)
    else:
        terminal = SimulatedTerminal(hwnd=0)
        return session.replay(terminal=terminal)
