"""
uia_echo_filter_probe.py — UIA echo filter + TermControl readback proof

Goal: prove SelfConnect can separate injected WM_CHAR text (local echo) from
real terminal output using UIA TextChanged events / TextPattern polling on a
controlled Windows Terminal / ConPTY throwaway target.

Does NOT touch any active agent terminal. Spawns a throwaway conhost target
and terminates it automatically on completion.

Usage:
    python experiments/win32_probe/uia_echo_filter_probe.py
    python experiments/win32_probe/uia_echo_filter_probe.py --hwnd 0x1A2B3C
    python experiments/win32_probe/uia_echo_filter_probe.py --no-spawn --hwnd 0x1A2B3C

Outputs a JSON result record and a human-readable verdict line.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from enum import Enum

# ── result types ──────────────────────────────────────────────────────────────

class ProbeResult(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NA   = "NA"


@dataclass
class FilterRecord:
    """Structured record for one probe run."""
    result:        ProbeResult = ProbeResult.NA
    na_reason:     str         = ""

    hwnd:          int   = 0
    pid:           int   = 0
    uia_method:    str   = ""         # "TextChanged_event" | "TextPattern_poll" | ""

    nonce:         str   = ""
    sent_hash:     str   = ""         # SHA-256 hex of injected nonce bytes
    observed_hash: str   = ""         # SHA-256 hex of delta text observed

    echo_text:     str   = ""         # portion identified as local echo
    output_text:   str   = ""         # portion identified as external output
    raw_delta:     str   = ""         # full delta text (trimmed to 512 chars)

    timestamp_send:  float = 0.0      # time.time() at injection
    timestamp_first: float = 0.0      # time.time() when first delta observed
    latency_ms:      float = 0.0      # timestamp_first - timestamp_send, in ms

    uia_available:   bool = False
    event_supported: bool = False


# ── echo filter ───────────────────────────────────────────────────────────────

class EchoFilter:
    """Classify UIA text deltas as local echo vs external output.

    A delta is classified as ECHO when it starts with the injected nonce
    (after stripping leading whitespace and common terminal prompt artefacts).
    Text that follows the nonce in the same delta is classified as OUTPUT.

    The injector can never produce external OUTPUT — anything after the echo
    position is assumed to be terminal feedback (prompt redraw, command echo,
    etc.) which is treated as output for measurement purposes.
    """

    _STRIP = "\r\n \t"

    @staticmethod
    def classify_delta(
        delta: str,
        nonce: str,
    ) -> tuple[str, str, str]:
        """Return (echo_part, output_part, classification).

        classification:
          "echo"    — nonce found; split applied
          "output"  — nonce not found; whole delta is external output
          "empty"   — delta is empty/whitespace only
        """
        if not delta.strip():
            return ("", "", "empty")

        stripped = delta.lstrip(EchoFilter._STRIP)
        pos = stripped.find(nonce)
        if pos == 0:
            echo_end = pos + len(nonce)
            echo_part   = stripped[:echo_end]
            output_part = stripped[echo_end:].lstrip(EchoFilter._STRIP)
            return (echo_part, output_part, "echo")

        # Nonce anywhere else in delta — treat prefix as echo, rest as output
        if nonce in stripped:
            pos = stripped.index(nonce)
            echo_part   = stripped[pos: pos + len(nonce)]
            output_part = (stripped[:pos] + stripped[pos + len(nonce):]).strip(EchoFilter._STRIP)
            return (echo_part, output_part, "echo")

        return ("", delta.strip(EchoFilter._STRIP), "output")


# ── UIA helpers ───────────────────────────────────────────────────────────────

#  UIA property/event IDs (stable; part of the public UIA MSDN contract)
_UIA_IsTextPatternAvailableId = 30040
_UIA_TextChangedEventId       = 20015
_TreeScope_Subtree            = 7


def _get_uia():
    import comtypes.client as _cc  # type: ignore
    mod = _cc.GetModule("UIAutomationCore.dll")
    return _cc.CreateObject(
        "{FF48DBA4-60EF-4201-AA87-54103EEF594E}",
        interface=mod.IUIAutomation,
    ), mod


def _find_term_control(uia, mod, hwnd: int):
    """Return (element, TextPattern) for the TermControl with the longest buffer.

    Key insight from session-9 work (preserved in memory):
    - Filter by IsTextPatternAvailable (prop 30040), NOT ControlType.
    - TermControl is ControlType=Text(50020); filtering by Document misses it.
    - Use FindAll + pick LONGEST text (FindFirst returns the tab-title header).
    - Reads are focus-independent on ConPTY surfaces.
    """
    root  = uia.ElementFromHandle(hwnd)
    cond  = uia.CreatePropertyCondition(_UIA_IsTextPatternAvailableId, True)
    found = root.FindAll(_TreeScope_Subtree, cond)
    best_el, best_tp, best_len = None, None, -1
    for i in range(found.Length):
        el = found.GetElement(i)
        try:
            # GetCurrentPattern returns IUnknown; QueryInterface to typed interface.
            tp   = el.GetCurrentPattern(10014).QueryInterface(mod.IUIAutomationTextPattern)
            text = tp.DocumentRange.GetText(-1)
            if len(text) > best_len:
                best_el, best_tp, best_len = el, tp, len(text)
        except Exception:
            pass
    return best_el, best_tp, best_len


# ── throwaway target ──────────────────────────────────────────────────────────

_WT_PATHS = [
    r"C:\Users\techai\AppData\Local\Microsoft\WindowsApps\wt.exe",
    r"C:\Program Files\WindowsApps\Microsoft.WindowsTerminal_1.21.3231.0_x64__8wekyb3d8bbwe\wt.exe",
]


def _find_wt() -> str | None:
    import os
    for p in _WT_PATHS:
        if os.path.exists(p):
            return p
    # Last-resort: glob WindowsApps
    import glob
    hits = glob.glob(r"C:\Users\techai\AppData\Local\Microsoft\WindowsApps\wt.exe")
    return hits[0] if hits else None


def _spawn_conhost() -> tuple[int, subprocess.Popen | None]:
    """Spawn an isolated throwaway terminal safe for WM_CHAR injection.

    Tries Windows Terminal tab first (ConPTY, UIA-visible). Falls back to
    standalone conhost. Returns (hwnd, proc_or_None) — proc is None when
    the window was spawned inside an existing WT instance.
    """
    sys.path.insert(0, str(_repo_root()))
    from self_connect import list_windows  # type: ignore[import]

    title = f"SC_PROBE_{uuid.uuid4().hex[:6].upper()}"
    wt = _find_wt()
    proc = None

    if wt:
        # WT new-tab: the new tab window is owned by the existing WT process.
        subprocess.Popen(
            [wt, "-w", "0", "new-tab", "--title", title,
             "cmd.exe", "/K", "echo SC_TARGET_READY"],
            creationflags=subprocess.DETACHED_PROCESS,
        )
    else:
        proc = subprocess.Popen(
            [r"C:\Windows\System32\conhost.exe",
             r"C:\Windows\System32\cmd.exe", "/K", "echo SC_TARGET_READY"],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )

    deadline = time.time() + 20
    hwnd = None
    while time.time() < deadline and hwnd is None:
        time.sleep(0.5)
        for w in list_windows():
            if wt:
                # WT tab: match by window title
                if title in w.title:
                    hwnd = w.hwnd
                    break
            else:
                # Standalone conhost: match by PID
                if proc and getattr(w, "pid", 0) == proc.pid:
                    hwnd = w.hwnd
                    break

    if hwnd is None:
        if proc:
            proc.terminate()
        raise RuntimeError(
            f"Throwaway target {title!r} did not appear within 20 s. "
            "On headless/service sessions without a visible desktop, "
            "pass --hwnd with an existing throwaway terminal hwnd."
        )
    time.sleep(1.5)
    return hwnd, proc


def _repo_root():
    from pathlib import Path
    return Path(__file__).parent.parent.parent


# ── probe core ────────────────────────────────────────────────────────────────

def run_probe(
    target_hwnd: int = 0,
    *,
    timeout_s: float = 12.0,
    poll_interval_s: float = 0.25,
) -> FilterRecord:
    """Run the echo filter probe against target_hwnd (or spawn throwaway).

    Returns a FilterRecord with result=PASS/FAIL/NA.
    Callers that pass target_hwnd=0 will have a conhost spawned and
    terminated automatically.
    """
    rec = FilterRecord()
    proc = None

    # ── prerequisite checks ───────────────────────────────────────────────────
    if sys.platform != "win32":
        rec.na_reason = "Win32 platform required; skipped on non-Windows"
        return rec

    try:
        import comtypes  # type: ignore
        import comtypes.client  # type: ignore
        import pythoncom  # type: ignore
    except ImportError as exc:
        rec.na_reason = f"comtypes/pythoncom not installed: {exc}"
        return rec

    sys.path.insert(0, str(_repo_root()))
    try:
        from self_connect import list_windows, send_string  # type: ignore[import]
    except ImportError as exc:
        rec.na_reason = f"self_connect not importable: {exc}"
        return rec

    # ── target window ─────────────────────────────────────────────────────────
    if target_hwnd == 0:
        try:
            target_hwnd, proc = _spawn_conhost()
        except RuntimeError as exc:
            rec.na_reason = str(exc)
            return rec

    wins = list_windows()
    target_win = next((w for w in wins if w.hwnd == target_hwnd), None)
    if target_win is None:
        rec.na_reason = f"hwnd {target_hwnd:#x} not found in window list"
        if proc:
            proc.terminate()
        return rec

    rec.hwnd = target_hwnd
    rec.pid  = getattr(target_win, "pid", 0)

    # ── UIA init ──────────────────────────────────────────────────────────────
    import pythoncom  # type: ignore
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass

    try:
        uia, mod = _get_uia()
    except Exception as exc:
        rec.na_reason = f"IUIAutomation CreateObject failed: {exc}"
        if proc:
            proc.terminate()
        return rec

    try:
        el, tp, baseline_len = _find_term_control(uia, mod, target_hwnd)
    except Exception as exc:
        rec.na_reason = f"UIA tree walk failed: {exc}"
        pythoncom.CoUninitialize()
        if proc:
            proc.terminate()
        return rec

    if el is None or tp is None:
        rec.na_reason = (
            "No TextPattern element found on target hwnd. "
            "Possible causes: ConPTY not started, UIA provider not loaded, "
            "or window is not a supported terminal surface."
        )
        pythoncom.CoUninitialize()
        if proc:
            proc.terminate()
        return rec

    rec.uia_available = True

    # ── nonce + baseline ──────────────────────────────────────────────────────
    nonce = f"SC_ECHO_{uuid.uuid4().hex[:8].upper()}"
    rec.nonce     = nonce
    rec.sent_hash = hashlib.sha256(nonce.encode()).hexdigest()

    baseline_text = tp.DocumentRange.GetText(-1)
    baseline_len  = len(baseline_text)

    # ── try TextChanged event ─────────────────────────────────────────────────
    fired_deltas: list[str] = []
    handler_obj = None
    event_registered = False

    try:
        from typing import ClassVar

        import comtypes  # type: ignore

        class _Handler(comtypes.COMObject):
            _com_interfaces_: ClassVar[list] = [mod.IUIAutomationEventHandler]

            def IUIAutomationEventHandler_HandleAutomationEvent(self, sender, eventId):
                try:
                    current = tp.DocumentRange.GetText(-1)
                    delta   = current[baseline_len:]
                    if delta.strip():
                        fired_deltas.append(delta)
                except Exception:
                    pass

        handler_obj = _Handler()
        uia.AddAutomationEventHandler(
            _UIA_TextChangedEventId, el, 1, None, handler_obj
        )
        event_registered   = True
        rec.event_supported = True
    except Exception:
        rec.event_supported = False

    # ── inject nonce ──────────────────────────────────────────────────────────
    rec.timestamp_send = time.time()
    # \r submits the nonce to cmd.exe; without it the chars sit in readline
    # buffer and ConPTY does not commit them to the UIA scrollback until Enter.
    send_string(target_win, nonce + "\r")

    # ── wait for text change (event or poll) ──────────────────────────────────
    deadline     = rec.timestamp_send + timeout_s
    poll_deltas: list[str] = []
    prev_len = baseline_len

    while time.time() < deadline:
        if event_registered:
            try:
                pythoncom.PumpWaitingMessages()
            except Exception:
                pass
        if fired_deltas:
            break
        # parallel poll regardless of event (catches cases where event fires late)
        try:
            current = tp.DocumentRange.GetText(-1)
            if len(current) > prev_len:
                delta = current[baseline_len:]
                if delta.strip():
                    poll_deltas.append(delta)
                    prev_len = len(current)
                    if not event_registered:
                        break
        except Exception:
            pass
        time.sleep(poll_interval_s)

    if event_registered:
        try:
            uia.RemoveAllEventHandlers()
        except Exception:
            pass

    try:
        pythoncom.CoUninitialize()
    except Exception:
        pass

    # Close the throwaway target.
    # WT tab: send exit\r to terminate the cmd session (closes the tab).
    # Standalone proc: terminate the process.
    try:
        send_string(target_win, "exit\r")
        time.sleep(0.5)
    except Exception:
        pass
    if proc:
        proc.terminate()

    # ── pick best delta ───────────────────────────────────────────────────────
    if fired_deltas:
        delta          = fired_deltas[0]
        rec.uia_method = "TextChanged_event"
    elif poll_deltas:
        delta          = poll_deltas[0]
        rec.uia_method = "TextPattern_poll"
    else:
        rec.na_reason = (
            f"No text change observed within {timeout_s:.0f} s. "
            "Possible causes: TextChanged not supported on this ConPTY surface, "
            "COM message pump not processing (single-threaded model), "
            "or injection did not echo (e.g. non-interactive cmd session)."
        )
        return rec

    if rec.timestamp_first == 0.0:
        rec.timestamp_first = time.time()
    rec.latency_ms = (rec.timestamp_first - rec.timestamp_send) * 1000

    # ── echo filter ───────────────────────────────────────────────────────────
    echo_part, output_part, classification = EchoFilter.classify_delta(delta, nonce)

    rec.raw_delta     = delta[:512]
    rec.echo_text     = echo_part[:256]
    rec.output_text   = output_part[:256]
    rec.observed_hash = hashlib.sha256(delta.encode()).hexdigest()

    if classification == "echo":
        rec.result = ProbeResult.PASS
    elif classification == "output":
        # Nonce did not appear in delta — may be buffered elsewhere
        rec.result    = ProbeResult.FAIL
        rec.na_reason = (
            f"Nonce {nonce!r} not found in first delta. "
            "Echo may have been suppressed or absorbed into a prior buffer. "
            "Try --timeout 20 or inspect raw_delta."
        )
    else:
        rec.result    = ProbeResult.NA
        rec.na_reason = "Delta was empty after polling — no text to classify."

    return rec


# ── report ────────────────────────────────────────────────────────────────────

def _report(rec: FilterRecord, *, verbose: bool = False) -> None:
    verdict = f"[UIA_ECHO_FILTER] result={rec.result.value}"
    if rec.na_reason:
        verdict += f" | {rec.na_reason}"
    else:
        verdict += (
            f" | method={rec.uia_method}"
            f" | latency={rec.latency_ms:.0f}ms"
            f" | sent={rec.sent_hash[:12]}..."
            f" | observed={rec.observed_hash[:12]}..."
        )
    print(verdict)

    if verbose or rec.result != ProbeResult.PASS:
        print(json.dumps(
            {k: (v.value if isinstance(v, ProbeResult) else v)
             for k, v in rec.__dict__.items()},
            indent=2,
        ))


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Prove UIA echo filtering for SelfConnect terminal readback"
    )
    ap.add_argument(
        "--hwnd", type=lambda x: int(x, 0), default=0,
        help="target window handle (0 = spawn throwaway conhost)",
    )
    ap.add_argument("--no-spawn", action="store_true",
                    help="require --hwnd; do not spawn a throwaway target")
    ap.add_argument("--timeout", type=float, default=12.0,
                    help="seconds to wait for text change (default: 12)")
    ap.add_argument("--verbose", action="store_true",
                    help="print full JSON record regardless of result")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    if args.no_spawn and args.hwnd == 0:
        print("ERROR: --no-spawn requires --hwnd", file=sys.stderr)
        return 2
    rec = run_probe(args.hwnd, timeout_s=args.timeout)
    _report(rec, verbose=args.verbose)
    return 0 if rec.result == ProbeResult.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
