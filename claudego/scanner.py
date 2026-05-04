"""
scanner.py — Terminal monitoring loop for ClaudeGo.

Wraps approval_partner logic. Runs in a background thread.
Emits ScanEvent objects to registered callbacks when:
  - A new Claude Code terminal is discovered
  - A terminal disappears
  - An approval prompt is detected
  - A decision is made (auto-approved / auto-denied / unknown)
  - A terminal goes idle for too long (stuck detection)
"""

from __future__ import annotations

# ── Path setup — work from selfconnect/ root ──────────────────────────────────
import pathlib
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

_ROOT = pathlib.Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from approval_partner import (  # type: ignore[import]  # noqa: E402
    PartnerConfig,
    decide,
    extract_tool_call,
    find_claude_terminals,
    has_approval_prompt,
    inject_response,
)
from self_connect import WindowTarget, get_text_uia  # type: ignore[import]  # noqa: E402

# ── Event model ───────────────────────────────────────────────────────────────

EVENT_TERMINAL_DISCOVERED = "terminal_discovered"
EVENT_TERMINAL_LOST       = "terminal_lost"
EVENT_APPROVAL_NEEDED     = "approval_needed"
EVENT_AUTO_APPROVED       = "auto_approved"
EVENT_AUTO_DENIED         = "auto_denied"
EVENT_UNKNOWN_TOOL        = "unknown_tool"
EVENT_MANUAL_APPROVED     = "manual_approved"
EVENT_MANUAL_DENIED       = "manual_denied"
EVENT_STUCK               = "stuck"


@dataclass
class ScanEvent:
    event_type: str
    hwnd: int
    title: str
    tool_call: Optional[str] = None
    rule_matched: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "hwnd": self.hwnd,
            "title": self.title,
            "tool_call": self.tool_call,
            "rule_matched": self.rule_matched,
            "timestamp": self.timestamp,
        }


# ── Terminal state ─────────────────────────────────────────────────────────────

@dataclass
class TerminalState:
    hwnd: int
    title: str
    status: str = "idle"          # idle | working | approval_needed | stuck | unknown
    pending_tool: Optional[str] = None
    last_activity: float = field(default_factory=time.time)
    last_approval_time: float = 0.0


# ── Scanner ───────────────────────────────────────────────────────────────────

class Scanner:
    """
    Polls all Claude Code terminals and emits events to registered callbacks.

    Thread-safe: callbacks are called from the scanner thread.
    Dashboard registers a callback that relays events to WebSocket clients.
    Manual approve/deny can be called from any thread.
    """

    def __init__(
        self,
        cfg: Optional[PartnerConfig] = None,
        poll_interval: float = 2.0,
        stuck_timeout: float = 300.0,
        dry_run: bool = False,
    ) -> None:
        self.cfg = cfg or PartnerConfig()
        self.poll_interval = poll_interval
        self.stuck_timeout = stuck_timeout
        self.dry_run = dry_run

        self._terminals: dict[int, TerminalState] = {}
        self._audit_log: list[ScanEvent] = []
        self._callbacks: list[Callable[[ScanEvent], None]] = []
        self._running = False

        # Map hwnd → WindowTarget (kept for inject_response calls)
        self._win_map: dict[int, WindowTarget] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    def add_callback(self, cb: Callable[[ScanEvent], None]) -> None:
        self._callbacks.append(cb)

    def get_terminals(self) -> list[dict]:
        return [
            {
                "hwnd": t.hwnd,
                "title": t.title,
                "status": t.status,
                "pending_tool": t.pending_tool,
                "last_activity": t.last_activity,
            }
            for t in self._terminals.values()
        ]

    def get_audit_log(self, limit: int = 100) -> list[dict]:
        return [e.to_dict() for e in self._audit_log[-limit:]]

    def get_rules(self) -> dict:
        return {
            "allow": list(self.cfg.allow_patterns),
            "deny": list(self.cfg.deny_patterns),
            "default_action": self.cfg.default_action,
        }

    def set_rules(self, allow: list[str], deny: list[str], default_action: str = "escalate") -> None:
        self.cfg.allow_patterns = allow
        self.cfg.deny_patterns = deny
        self.cfg.default_action = default_action

    def manual_approve(self, hwnd: int) -> bool:
        """Inject 'y' into the terminal with the given hwnd. Returns True if found."""
        win = self._win_map.get(hwnd)
        if not win:
            return False
        if not self.dry_run:
            from self_connect import send_string  # type: ignore[import]
            send_string(win, "y\r")
        state = self._terminals.get(hwnd)
        tool = state.pending_tool if state else None
        evt = ScanEvent(EVENT_MANUAL_APPROVED, hwnd, win.title, tool_call=tool)
        self._record(evt)
        if state:
            state.status = "working"
            state.pending_tool = None
            state.last_approval_time = time.monotonic()
        return True

    def manual_deny(self, hwnd: int) -> bool:
        """Inject 'n' into the terminal with the given hwnd. Returns True if found."""
        win = self._win_map.get(hwnd)
        if not win:
            return False
        if not self.dry_run:
            from self_connect import send_string  # type: ignore[import]
            send_string(win, "n\r")
        state = self._terminals.get(hwnd)
        tool = state.pending_tool if state else None
        evt = ScanEvent(EVENT_MANUAL_DENIED, hwnd, win.title, tool_call=tool)
        self._record(evt)
        if state:
            state.status = "idle"
            state.pending_tool = None
            state.last_approval_time = time.monotonic()
        return True

    def stop(self) -> None:
        self._running = False

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Blocking loop. Run in a background daemon thread."""
        self._running = True
        while self._running:
            try:
                self._tick()
            except Exception as exc:
                print(f"[claudego scanner] ERROR: {exc}")
            time.sleep(self.poll_interval)

    def _tick(self) -> None:
        now_mono = time.monotonic()
        now_wall = time.time()

        live_terminals = find_claude_terminals()
        live_hwnds = {w.hwnd for w in live_terminals}

        # Update win_map
        for win in live_terminals:
            self._win_map[win.hwnd] = win

        # Discover new terminals
        for win in live_terminals:
            if win.hwnd not in self._terminals:
                state = TerminalState(hwnd=win.hwnd, title=win.title)
                self._terminals[win.hwnd] = state
                evt = ScanEvent(EVENT_TERMINAL_DISCOVERED, win.hwnd, win.title)
                self._record(evt)

        # Detect lost terminals
        for hwnd in list(self._terminals.keys()):
            if hwnd not in live_hwnds:
                state = self._terminals.pop(hwnd)
                self._win_map.pop(hwnd, None)
                evt = ScanEvent(EVENT_TERMINAL_LOST, hwnd, state.title)
                self._record(evt)

        # Process each terminal
        for win in live_terminals:
            state = self._terminals[win.hwnd]
            state.title = win.title  # title may change

            # Cooldown: don't re-fire approval on same window too quickly
            if now_mono - state.last_approval_time < self.cfg.cooldown:
                continue

            if not has_approval_prompt(win.hwnd):
                # Stuck detection: working but no activity for a long time
                if (state.status == "working"
                        and now_wall - state.last_activity > self.stuck_timeout):
                    state.status = "stuck"
                    evt = ScanEvent(EVENT_STUCK, win.hwnd, win.title)
                    self._record(evt)
                elif state.status == "approval_needed":
                    # Prompt cleared without our action (user handled it manually)
                    state.status = "idle"
                    state.pending_tool = None
                continue

            # Approval prompt detected
            text = get_text_uia(win.hwnd) or ""
            tool_call = extract_tool_call(text)
            decision = decide(tool_call, self.cfg)

            state.last_activity = now_wall
            state.pending_tool = tool_call

            if decision is True:
                state.status = "working"
                state.pending_tool = None
                state.last_approval_time = now_mono
                if not self.dry_run:
                    inject_response(win, approve=True, cfg=self.cfg)
                # Find which rule matched
                rule = self._find_matching_rule(tool_call, allow=True)
                evt = ScanEvent(
                    EVENT_AUTO_APPROVED, win.hwnd, win.title,
                    tool_call=tool_call, rule_matched=rule,
                )
                self._record(evt)

            elif decision is False:
                state.status = "idle"
                state.pending_tool = None
                state.last_approval_time = now_mono
                if not self.dry_run:
                    inject_response(win, approve=False, cfg=self.cfg)
                rule = self._find_matching_rule(tool_call, allow=False)
                evt = ScanEvent(
                    EVENT_AUTO_DENIED, win.hwnd, win.title,
                    tool_call=tool_call, rule_matched=rule,
                )
                self._record(evt)

            else:
                # Unknown — needs human decision
                if state.status != "approval_needed":
                    state.status = "approval_needed"
                    evt = ScanEvent(
                        EVENT_UNKNOWN_TOOL, win.hwnd, win.title,
                        tool_call=tool_call,
                    )
                    self._record(evt)
                    # Also emit approval_needed for dashboard UI
                    evt2 = ScanEvent(
                        EVENT_APPROVAL_NEEDED, win.hwnd, win.title,
                        tool_call=tool_call,
                    )
                    self._record(evt2)

    def _find_matching_rule(self, tool_call: Optional[str], allow: bool) -> Optional[str]:
        if tool_call is None:
            return None
        import fnmatch
        patterns = self.cfg.allow_patterns if allow else self.cfg.deny_patterns
        for p in patterns:
            if fnmatch.fnmatch(tool_call, p):
                return p
        return None

    def _record(self, evt: ScanEvent) -> None:
        self._audit_log.append(evt)
        if len(self._audit_log) > 1000:
            self._audit_log = self._audit_log[-500:]
        for cb in self._callbacks:
            try:
                cb(evt)
            except Exception as exc:
                print(f"[claudego scanner] callback error: {exc}")
