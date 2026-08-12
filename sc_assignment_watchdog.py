"""Fail-closed operational wrapper around the canonical assignment protocol."""

from __future__ import annotations

import copy
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sc_assignment_protocol import (
    ASSIGNMENT_STATES,
    AssignmentStateStore,
    AssignmentVerificationError,
    verify_consume_state_receipt,
)
from sc_guarded_submit import TargetIdentity


@dataclass(frozen=True)
class Observation:
    state: str
    evidence: str
    source: str
    authenticated: bool = False
    receipt: dict[str, Any] | None = None


_REFUSAL = re.compile(r"\A(?:status:\s*)?(?:refused|rejected|denied|unable)\b", re.I)
_BLOCKED = re.compile(r"\A(?:status:\s*)?(?:blocked|approval required|permission required|rate limited?)\b", re.I)
_WORKING = re.compile(r"\A(?:status:\s*)?(?:working|thinking|running|in progress|processing)\b", re.I)
_COMPLETED = re.compile(r"\A(?:status:\s*)?(?:completed|done|finished)\Z", re.I)
_BARE_SHELL = re.compile(r"\A(?:PS [A-Za-z]:\\[^>]*>|\$)\s*\Z", re.I)


class AssignmentWatchdog:
    """Poll authenticated state receipts; never injects recovery or assignment text."""

    def __init__(
        self,
        *,
        store: AssignmentStateStore,
        receipt_reader: Callable[[], Any],
        read_uia: Callable[[int], str],
        read_ocr: Callable[[int], str],
        capture: Callable[[int], Any],
        alert_coordinator: Callable[[dict[str, Any]], None],
        source_guard: Callable[[Any], bool],
        target_guard: Callable[[TargetIdentity], bool],
        verification_resolver: Callable[[], dict[str, Any]],
        receipt_acknowledger: Callable[[dict[str, Any]], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._receipt_reader = receipt_reader
        self._read_uia, self._read_ocr = read_uia, read_ocr
        self._capture, self._alert = capture, alert_coordinator
        self._source_guard, self._target_guard = source_guard, target_guard
        if not callable(verification_resolver):
            raise TypeError("a live verification and revocation resolver is required")
        self._verification_resolver = verification_resolver
        self._receipt_acknowledger = receipt_acknowledger
        self._clock = clock
        self._monitor_lock = threading.Lock()
        self._last_screen = Observation("submitted", "no authenticated receipt yet", "transport")

    @staticmethod
    def classify_screen(text: str, *, source: str) -> Observation:
        """Classify advisory screen evidence; it never authenticates progress."""
        bounded = (text or "")[-2_000:]
        lines = [line.strip() for line in bounded.splitlines() if line.strip()]
        live_line = lines[-1] if lines else ""
        if _REFUSAL.search(live_line):
            return Observation("refused", live_line, source)
        if _BLOCKED.search(live_line) or _BARE_SHELL.search(live_line):
            return Observation("blocked", live_line, source)
        if _COMPLETED.search(live_line):
            return Observation("completed", live_line, source)
        if _WORKING.search(live_line):
            return Observation("working", live_line, source)
        if "\u203a" in live_line or live_line.startswith("Run /"):
            return Observation("unsubmitted", live_line, source)
        return Observation("submitted", live_line, source)

    def _screen_evidence(self, hwnd: int) -> Observation:
        read_errors: list[str] = []
        try:
            text = self._read_uia(hwnd) or ""
        except Exception as exc:
            read_errors.append(type(exc).__name__)
            text = ""
        if text:
            return self.classify_screen(text, source="uia")
        try:
            text = self._read_ocr(hwnd) or ""
        except Exception as exc:
            read_errors.append(type(exc).__name__)
            text = ""
        if not text:
            evidence = "screen unreadable" if read_errors else "screen text empty"
            return Observation("blocked", evidence, "screen")
        return self.classify_screen(text, source="ocr")

    def _escalate(self, *, hwnd: int, state: str, reason: str) -> None:
        capture = None
        capture_error = None
        try:
            capture = self._capture(hwnd)
        except Exception as exc:
            capture_error = f"{type(exc).__name__}: {exc}"
        event = {
            "hwnd": hwnd,
            "state": state,
            "reason": reason,
            "screen_evidence": self._last_screen,
            "capture": capture,
        }
        if capture_error:
            event["capture_error"] = capture_error
        try:
            self._alert(event)
        except Exception:
            # A broken alert sink cannot turn an unsafe result into success.
            pass

    def monitor(
        self,
        *,
        hwnd: int,
        assignment: dict[str, Any],
        assignment_source: Any,
        timeout_seconds: float = 30.0,
        poll_seconds: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
    ) -> Observation:
        """Return only authenticated terminal states; all failures are blocked."""
        if not self._monitor_lock.acquire(blocking=False):
            self._escalate(hwnd=hwnd, state="blocked", reason="monitor_reentrancy_rejected")
            return Observation("blocked", "assignment monitor is already active", "configuration")
        try:
            return self._monitor_locked(
                hwnd=hwnd,
                assignment=assignment,
                assignment_source=assignment_source,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
                sleep=sleep,
            )
        finally:
            self._monitor_lock.release()

    def _monitor_locked(
        self,
        *,
        hwnd: int,
        assignment: dict[str, Any],
        assignment_source: Any,
        timeout_seconds: float,
        poll_seconds: float,
        sleep: Callable[[float], None],
    ) -> Observation:
        if not isinstance(assignment, dict):
            self._escalate(hwnd=hwnd, state="blocked", reason="malformed_assignment_input")
            return Observation("blocked", "assignment input is malformed", "protocol")
        if not callable(self._receipt_reader) or not callable(self._source_guard) or not callable(self._target_guard):
            self._escalate(hwnd=hwnd, state="blocked", reason="required_guard_or_reader_missing")
            return Observation("blocked", "receipt reader and both guards are required", "configuration")

        try:
            initial_verification = self._resolve_verification()
        except Exception as exc:
            self._escalate(hwnd=hwnd, state="blocked", reason=f"revocation_resolution_failed:{type(exc).__name__}")
            return Observation("blocked", str(exc), "configuration")
        expected_target = initial_verification.get("expected_target_identity")
        if type(expected_target) is not TargetIdentity or expected_target.hwnd != hwnd:
            self._escalate(hwnd=hwnd, state="blocked", reason="expected_target_identity_missing_or_mismatched")
            return Observation("blocked", "exact expected target identity is required", "configuration")

        try:
            timeout = float(timeout_seconds)
            interval = float(poll_seconds)
            if timeout <= 0 or interval <= 0:
                raise AssignmentVerificationError("invalid receipt polling policy")
            deadline = self._clock() + timeout
            verified = None
            while self._clock() < deadline:
                self._guard_pair(assignment_source, expected_target, "before_receipt_read")
                self._last_screen = self._screen_evidence(hwnd)
                raw = self._receipt_reader()
                self._guard_pair(assignment_source, expected_target, "after_receipt_read")
                if raw is not None:
                    if type(raw) is not dict:
                        raise AssignmentVerificationError("receipt source returned a non-dict")
                    verification = self._resolve_verification()
                    if verification.get("expected_target_identity") != expected_target:
                        raise AssignmentVerificationError("live expected target identity changed")
                    verified = verify_consume_state_receipt(
                        copy.deepcopy(raw),
                        copy.deepcopy(assignment),
                        store=self._store,
                        **verification,
                    )
                    self._guard_pair(assignment_source, expected_target, "after_receipt_verify")
                    acknowledge = getattr(self._receipt_reader, "acknowledge", None)
                    if callable(acknowledge):
                        acknowledge(raw)
                    if self._receipt_acknowledger is not None:
                        self._receipt_acknowledger(copy.deepcopy(raw))
                    if verified["state"] in {"completed", "blocked", "rejected"}:
                        break
                sleep(min(interval, max(0.0, deadline - self._clock())))
            if verified is None or verified["state"] not in {"completed", "blocked", "rejected"}:
                raise TimeoutError("authenticated assignment receipt deadline expired")
        except TimeoutError:
            self._escalate(hwnd=hwnd, state="blocked", reason="timeout")
            return Observation("blocked", "authenticated receipt timeout", "timeout")
        except Exception as exc:
            self._escalate(hwnd=hwnd, state="blocked", reason=f"protocol_failure:{type(exc).__name__}")
            return Observation("blocked", str(exc), "protocol")

        state = str(verified["state"])
        if state in {"blocked", "rejected"}:
            self._last_screen = self._screen_evidence(hwnd)
            self._escalate(
                hwnd=hwnd,
                state="blocked" if state == "blocked" else "refused",
                reason="authenticated_receipt_terminal_failure",
            )
        return Observation(
            "refused" if state == "rejected" else state,
            "canonical authenticated state receipt",
            "receipt",
            True,
            copy.deepcopy(verified),
        )

    def _resolve_verification(self) -> dict[str, Any]:
        verification = self._verification_resolver()
        if type(verification) is not dict:
            raise AssignmentVerificationError("live verification resolver returned a non-dict")
        for name in ("revoked_coordinator_key_ids", "revoked_seat_key_ids"):
            if type(verification.get(name)) is not frozenset:
                raise AssignmentVerificationError(f"live {name} snapshot is required")
        return copy.deepcopy(verification)

    def _guard_pair(self, source: Any, target: TargetIdentity, stage: str) -> None:
        try:
            source_guarded = self._source_guard(source) is True
            target_guarded = self._target_guard(target) is True
        except Exception as exc:
            raise AssignmentVerificationError(f"assignment guard failed at {stage}") from exc
        if not source_guarded or not target_guarded:
            raise AssignmentVerificationError(f"assignment guard failed closed at {stage}")


__all__ = ["ASSIGNMENT_STATES", "AssignmentWatchdog", "Observation"]
