"""Real two-tab Windows Terminal active-tab guard drill.

The drill owns and destroys its Windows Terminal window. It proves bounded UIA
identity checks and deliberately does not claim exclusive per-tab hardware
routing: PostMessage/SendInput still address the active window path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import ctypes
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sc_terminal_tab as tabs  # noqa: E402
from sc_guarded_submit import TargetIdentity  # noqa: E402
from self_connect import (  # noqa: E402
    WT_INPUT_CLASS,
    _send_char_postmessage,
    find_child_by_class,
    list_windows,
)


_UIA_BUTTON_CONTROL_TYPE_ID = 50000
_UIA_INVOKE_PATTERN_ID = 10000


def _canonical_source_digest(path: Path) -> str:
    raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(raw).hexdigest()


def _wait_until(predicate, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise TimeoutError("controlled Windows Terminal state did not converge")


def _select(uia: Any, module: Any, item: Any) -> None:
    pattern = item.GetCurrentPattern(10010).QueryInterface(module.IUIAutomationSelectionItemPattern)
    pattern.Select()
    _wait_until(lambda: tabs._selected(item))


def _reorder_with_terminal_command(
    uia: Any, root: Any, retained: Any, window_name: str
) -> tuple[int, int]:
    del window_name
    from pywinauto.keyboard import send_keys

    before = tabs._tab_items(uia, root)
    old_index = next(index for index, item in enumerate(before) if uia.CompareElements(retained, item))
    root.SetFocus()
    send_keys("^+p")
    time.sleep(0.3)
    send_keys(
        "Move tab forward" if old_index == 0 else "Move tab backward",
        with_spaces=True,
    )
    send_keys("{ENTER}")

    def changed():
        current = tabs._tab_items(uia, root)
        index = next((i for i, item in enumerate(current) if uia.CompareElements(retained, item)), -1)
        return index if index >= 0 and index != old_index else None

    new_index = _wait_until(changed, timeout=5.0)
    return old_index, int(new_index)


def _close_retained_tab(uia: Any, module: Any, retained: Any) -> None:
    condition = uia.CreatePropertyCondition(30003, _UIA_BUTTON_CONTROL_TYPE_ID)
    buttons = retained.FindAll(4, condition)
    for index in range(buttons.Length):
        button = buttons.GetElement(index)
        if "close" not in str(button.CurrentName).lower():
            continue
        invoke = button.GetCurrentPattern(_UIA_INVOKE_PATTERN_ID).QueryInterface(
            module.IUIAutomationInvokePattern
        )
        invoke.Invoke()
        return
    raise RuntimeError("controlled tab close button did not expose InvokePattern")


def _spawn_controlled_window(window_name: str, duplicate_title: str) -> subprocess.Popen:
    command = [
        "wt.exe",
        "-w",
        window_name,
        "new-tab",
        "--title",
        duplicate_title,
        "cmd.exe",
        "/k",
        f"title {duplicate_title}",
        ";",
        "split-pane",
        "--title",
        duplicate_title,
        "cmd.exe",
        "/k",
        f"title {duplicate_title}",
        ";",
        "new-tab",
        "--title",
        duplicate_title,
        "cmd.exe",
        "/k",
        f"title {duplicate_title}",
    ]
    return subprocess.Popen(command)


def _terminate_owned(process: subprocess.Popen) -> None:
    try:
        parent = psutil.Process(process.pid)
        descendants = parent.children(recursive=True)
        parent.terminate()
        _gone, alive = psutil.wait_procs([parent, *descendants], timeout=3)
        for item in alive:
            item.kill()
    except psutil.Error:
        pass


def _close_owned_window(hwnd: int) -> None:
    ctypes.windll.user32.PostMessageW(int(hwnd), 0x0010, 0, 0)


def run() -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("Windows is required")
    nonce = secrets.token_hex(6)
    window_name = f"SC_TAB_GUARD_{nonce}"
    duplicate_title = f"SC_DUP_{nonce}"
    process = _spawn_controlled_window(window_name, duplicate_title)
    owned_hwnd = 0
    try:
        window = _wait_until(
            lambda: next(
                (
                    item
                    for item in list_windows()
                    if duplicate_title in item.title
                ),
                None,
            ),
            timeout=15.0,
        )
        owned_hwnd = int(window.hwnd)
        target = TargetIdentity.from_window(window)
        uia, module = tabs._get_uia()
        root = uia.ElementFromHandle(target.hwnd)
        tab_items = tabs._tab_items(uia, root)
        if len(tab_items) != 2:
            raise AssertionError(f"expected two controlled tabs, got {len(tab_items)}")
        duplicate_titles = len({str(item.CurrentName) for item in tab_items}) == 1

        first = tab_items[0]
        _select(uia, module, first)
        guard = tabs.capture_active_terminal_tab(target, peer_birth_id=f"probe-{nonce}")
        retained = guard._retained_tab
        baseline = guard.checkpoint("baseline", select=False, deadline=time.monotonic() + 3)

        old_index, new_index = _reorder_with_terminal_command(
            uia, root, retained, window_name
        )
        reorder = guard.checkpoint("after-reorder", select=False, deadline=time.monotonic() + 3)

        current = tabs._tab_items(uia, root)
        other = next(item for item in current if not uia.CompareElements(retained, item))
        _select(uia, module, other)
        try:
            guard.checkpoint("wrong-tab", select=False, deadline=time.monotonic() + 3)
            wrong_tab_denied = False
        except tabs.TerminalTabGuardError:
            wrong_tab_denied = True
        guard.checkpoint("reselect", select=True, deadline=time.monotonic() + 3)

        subprocess.run(
            ["wt.exe", "-w", window_name, "move-focus", "left"],
            check=True,
            timeout=5,
        )
        try:
            guard.checkpoint("different-term-control", select=False, deadline=time.monotonic() + 3)
            term_control_mismatch_denied = False
        except tabs.TerminalTabGuardError:
            term_control_mismatch_denied = True
        subprocess.run(
            ["wt.exe", "-w", window_name, "move-focus", "right"],
            check=True,
            timeout=5,
        )
        guard.checkpoint("term-control-restored", select=False, deadline=time.monotonic() + 3)

        input_site = find_child_by_class(target.hwnd, WT_INPUT_CLASS)
        if not input_site:
            raise AssertionError("controlled Windows Terminal has no InputSite child")
        guard.checkpoint("before-postmessage", select=False, deadline=time.monotonic() + 3)
        postmessage_accepted = bool(_send_char_postmessage(input_site, "x"))
        _select(uia, module, other)
        try:
            guard.checkpoint("after-postmessage", select=False, deadline=time.monotonic() + 3)
            post_call_ambiguous = False
        except tabs.TerminalTabGuardError:
            post_call_ambiguous = postmessage_accepted

        guard.checkpoint("before-close", select=True, deadline=time.monotonic() + 3)
        old_runtime_id = guard.identity.tab_runtime_id
        _close_retained_tab(uia, module, retained)
        _wait_until(lambda: len(tabs._tab_items(uia, root)) == 1)
        try:
            guard.checkpoint("closed-tab", select=False, deadline=time.monotonic() + 3)
            stale_closed_denied = False
        except tabs.TerminalTabGuardError:
            stale_closed_denied = True
        subprocess.run(
            [
                "wt.exe",
                "-w",
                window_name,
                "new-tab",
                "--title",
                duplicate_title,
                "cmd.exe",
                "/k",
                f"title {duplicate_title}",
            ],
            check=True,
            timeout=5,
        )
        _wait_until(lambda: len(tabs._tab_items(uia, root)) == 2)
        new_selected = next(item for item in tabs._tab_items(uia, root) if tabs._selected(item))
        new_runtime_id = tabs._runtime_id(new_selected.GetRuntimeId(), "new tab RuntimeId")
        try:
            guard.checkpoint("reopened-tab", select=False, deadline=time.monotonic() + 3)
            stale_reopen_denied = False
        except tabs.TerminalTabGuardError:
            stale_reopen_denied = True

        checks = {
            "duplicate_titles_not_identity": duplicate_titles,
            "retained_compare_baseline": baseline["retained_compare"],
            "reorder_preserved_retained_identity": reorder["retained_compare"] and old_index != new_index,
            "wrong_active_tab_denied": wrong_tab_denied,
            "active_term_control_mismatch_denied": term_control_mismatch_denied,
            "post_call_change_is_ambiguous": post_call_ambiguous,
            "closed_tab_denied": stale_closed_denied,
            "reopened_tab_denied": stale_reopen_denied,
            "reopened_runtime_differs_in_recorded_run": new_runtime_id != old_runtime_id,
        }
        if not all(checks.values()):
            raise AssertionError(f"active-tab drill failed: {checks}")
        return {
            "schema": "selfconnect.windows-terminal-active-tab-guard-live.v1",
            "status": "PASS",
            "platform": "win32",
            "checks": checks,
            "identity": {
                "runtime_id_scope": guard.identity.runtime_id_scope,
                "peer_birth_id_sha256": hashlib.sha256(guard.identity.peer_birth_id.encode()).hexdigest(),
                "tab_runtime_id_length": len(guard.identity.tab_runtime_id),
                "term_control_runtime_id_length": len(guard.identity.term_control_runtime_id),
                "title_or_index_used_as_identity": False,
            },
            "transport_boundary": {
                "shared_input_site_hwnd_observed": True,
                "exclusive_per_tab_routing_claimed": False,
                "post_call_change": "ambiguous because queued input may already have taken effect",
                "preventive_alternative": "birth-id-bound named-pipe/control-plane input is out of scope",
            },
            "implementation": {
                "git_base_head": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, encoding="utf-8"
                ).strip(),
                "sc_terminal_tab_canonical_lf_sha256": _canonical_source_digest(ROOT / "sc_terminal_tab.py"),
                "sc_guarded_submit_canonical_lf_sha256": _canonical_source_digest(ROOT / "sc_guarded_submit.py"),
            },
            "raw_terminal_text_included": False,
        }
    finally:
        if owned_hwnd:
            _close_owned_window(owned_hwnd)
        _terminate_owned(process)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8", newline="\n")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
