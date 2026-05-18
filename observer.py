"""
Structured observability for SelfConnect mesh events.

Provides 7 event dataclasses, a MeshObserver with file/console/OTel backends,
an @observe decorator, and a module-level singleton.

Usage:
    from observer import MeshObserver, MessageSent, get_observer

    obs = get_observer()
    obs.emit(MessageSent(hwnd=12345, text="hello", mode="char"))
"""

from __future__ import annotations

import dataclasses
import functools
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Union

__all__ = [
    "ApprovalDecision",
    "FRPHit",
    "FRPLookup",
    "FRPMiss",
    "MeshEvent",
    "MeshObserver",
    "MessageSent",
    "PeerDiscovered",
    "PeerLost",
    "get_observer",
    "observe",
    "set_observer",
]

# Optional OpenTelemetry support
_OTEL_AVAILABLE = False
try:
    from opentelemetry import trace as _otel_trace
    _OTEL_AVAILABLE = True
except ImportError:
    pass

_log = logging.getLogger("selfconnect.observer")


# --- Event dataclasses ---


@dataclass(frozen=True)
class MessageSent:
    hwnd: int
    text: str
    mode: str = "auto"
    submitted: bool = False
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ApprovalDecision:
    hwnd: int
    tool: str
    decision: str  # "y", "n", "unknown"
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class PeerDiscovered:
    hwnd: int
    title: str
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class PeerLost:
    hwnd: int
    title: str
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class FRPLookup:
    error_text: str
    env_class: str
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class FRPHit:
    fingerprint: str
    title: str
    confidence: float
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class FRPMiss:
    fingerprint: str
    ts: float = field(default_factory=time.time)


MeshEvent = Union[
    MessageSent, ApprovalDecision, PeerDiscovered, PeerLost,
    FRPLookup, FRPHit, FRPMiss,
]


# --- Observer ---


class MeshObserver:
    """Emits mesh events to file, console, and/or OpenTelemetry."""

    def __init__(
        self,
        *,
        file: str | Path | None = None,
        console: bool = False,
        otel: bool = False,
    ) -> None:
        self._lock = threading.Lock()
        self._file_path = Path(file) if file else None
        self._file_handle = None
        self._console = console
        self._otel = otel and _OTEL_AVAILABLE

        if self._file_path:
            self._file_handle = open(self._file_path, "a", encoding="utf-8")  # noqa: SIM115

    def emit(self, event: MeshEvent) -> None:
        """Thread-safe event emission to all configured backends."""
        d = self._to_dict(event)
        with self._lock:
            if self._file_handle:
                self._write_file(d)
            if self._console:
                self._write_console(d)
            if self._otel:
                self._emit_otel(d)

    def _to_dict(self, event: MeshEvent) -> dict[str, Any]:
        d = dataclasses.asdict(event)
        d["kind"] = type(event).__name__
        return d

    def _write_file(self, d: dict[str, Any]) -> None:
        if self._file_handle:
            self._file_handle.write(json.dumps(d) + "\n")
            self._file_handle.flush()

    def _write_console(self, d: dict[str, Any]) -> None:
        _log.debug("%s", json.dumps(d))

    def _emit_otel(self, d: dict[str, Any]) -> None:
        if _OTEL_AVAILABLE:
            tracer = _otel_trace.get_tracer("selfconnect.observer")
            with tracer.start_as_current_span(d.get("kind", "unknown")) as span:
                for k, v in d.items():
                    span.set_attribute(k, str(v))

    def close(self) -> None:
        """Close the file handle if open."""
        with self._lock:
            if self._file_handle:
                self._file_handle.close()
                self._file_handle = None

    def __enter__(self) -> MeshObserver:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


# --- Module singleton ---

_default_observer: MeshObserver | None = None


def get_observer() -> MeshObserver:
    """Get or create the default observer (console-only by default)."""
    global _default_observer
    if _default_observer is None:
        _default_observer = MeshObserver(console=True)
    return _default_observer


def set_observer(obs: MeshObserver) -> None:
    """Replace the default observer."""
    global _default_observer
    _default_observer = obs


# --- Decorator ---


def observe(fn: Callable | None = None, *, event_factory: Callable | None = None):
    """
    Decorator for observing function calls.

    @observe — wraps with functools.wraps only (no event emitted)
    @observe(event_factory=factory) — factory(args, kwargs, return_value) -> MeshEvent
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = func(*args, **kwargs)
            if event_factory is not None:
                event = event_factory(args, kwargs, result)
                if event is not None:
                    get_observer().emit(event)
            return result
        return wrapper

    if fn is not None:
        # Called as @observe without parens
        return decorator(fn)
    return decorator
