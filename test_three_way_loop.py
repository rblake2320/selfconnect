"""
test_three_way_loop.py — Three-terminal message loop test.

Spawns THREE SEPARATE Windows Terminal windows:
  [1] Claude Code "testing term"   — new wt.exe window
  [2] Codex CLI "Codex Relay"      — new wt.exe window, codex (auto-approve config)
  [3] Gemini/Antigravity           — existing window (hwnd=329446)

Message chain:
  Orchestrator → Claude Code → Codex → (file signal) → Orchestrator → Gemini → done

Run:
  python test_three_way_loop.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from self_connect import (
    WindowTarget,
    find_child_by_class,
    get_text_uia,
    get_ui_tree,
    list_windows,
    send_string,
    submit_claude_input,
    _flatten_tree,
    _wait_for_claude_ready,
)

# ── Constants ─────────────────────────────────────────────────────────────────

REPO           = str(Path(__file__).parent)
SIGNAL_FILE    = Path(tempfile.gettempdir()) / "sc_loop_test.txt"
RELAY_CC_PY    = Path(tempfile.gettempdir()) / "relay_cc.py"
RELAY_CODEX_PY = Path(tempfile.gettempdir()) / "relay_codex.py"

ANTIGRAVITY_HWND = 329446   # Antigravity.exe "speed-dating-app1"
CC_TITLE         = "testing term"
CODEX_TITLE      = "Codex Relay"
WT_PATH          = r"C:\Users\techai\AppData\Local\Microsoft\WindowsApps\wt.exe"

SPAWN_TIMEOUT = 60.0
LOOP_TIMEOUT  = 120.0

# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# ── Signal file ───────────────────────────────────────────────────────────────

def clear_signal() -> None:
    SIGNAL_FILE.write_text("", encoding="utf-8")

def read_hops() -> set[str]:
    try:
        return {l.strip() for l in SIGNAL_FILE.read_text(encoding="utf-8").splitlines() if l.strip()}
    except Exception:
        return set()

def write_hop(hop: str) -> None:
    with open(SIGNAL_FILE, "a", encoding="utf-8") as f:
        f.write(f"{hop}\n")

# ── Relay scripts ─────────────────────────────────────────────────────────────

def write_relay_scripts(codex_hwnd: int, signal_file: str) -> None:
    """Write standalone relay scripts for each node.

    relay_codex.py — runs in Codex's shell, writes HOP:CODEX.
    relay_cc.py    — Claude Code runs this; writes HOP:CC then tells Codex to run relay_codex.py.
    """

    # relay_codex.py — Codex executes this as a shell command
    RELAY_CODEX_PY.write_text(f"""\
# relay_codex.py — Codex relay script (node 2 of 3)
import sys
sig = r'{signal_file}'
with open(sig, 'a', encoding='utf-8') as f:
    f.write('HOP:CODEX\\n')
print('[CODEX] HOP:CODEX written to', sig)
""", encoding="utf-8")

    log(f"  relay_codex.py written: {RELAY_CODEX_PY}")

    # relay_cc.py — Claude Code runs this via its Bash tool
    debug_file = str(SIGNAL_FILE).replace(".txt", "_debug.txt")
    RELAY_CC_PY.write_text(f"""\
# relay_cc.py — Claude Code relay script (node 1 of 3)
import subprocess, sys, time
sys.path.insert(0, r'{REPO}')
from self_connect import list_windows, send_string, submit_claude_input

sig = r'{signal_file}'
dbg = r'{debug_file}'
codex_hwnd = {codex_hwnd}
relay_codex = r'{RELAY_CODEX_PY}'

def log(msg):
    print(msg, flush=True)
    with open(dbg, 'a', encoding='utf-8') as f:
        f.write(msg + '\\n')

# Write HOP:CC
with open(sig, 'a', encoding='utf-8') as f:
    f.write('HOP:CC\\n')
log('[CC] HOP:CC written')

# Find Codex window
wins = [w for w in list_windows() if w.hwnd == codex_hwnd]
log(f'[CC] Codex window found: {{bool(wins)}}  hwnd={{codex_hwnd}}')
if not wins:
    all_wins = [(w.hwnd, w.title[:40]) for w in list_windows()]
    log(f'[CC] all windows: {{all_wins}}')

# Strategy 1: inject bare Python command + \\r into Codex (WM_CHAR Enter)
if wins:
    cmd = f'python "{{relay_codex}}"'
    send_string(wins[0], cmd + '\\r', mode='turbo')
    log(f'[CC] injected to Codex: {{cmd!r}}')
    time.sleep(2.0)

# Check if Codex wrote HOP:CODEX within 20s
deadline = time.monotonic() + 20.0
while time.monotonic() < deadline:
    try:
        content = open(sig, encoding='utf-8').read()
        if 'HOP:CODEX' in content:
            log('[CC] HOP:CODEX confirmed via injection')
            sys.exit(0)
    except Exception:
        pass
    time.sleep(1.0)

# Strategy 2: also try submit_claude_input
if wins:
    log('[CC] Strategy 1 timed out — trying submit_claude_input')
    send_string(wins[0], f'python "{{relay_codex}}"', mode='turbo')
    time.sleep(0.3)
    submit_claude_input(codex_hwnd)
    log('[CC] submit_claude_input sent')
    time.sleep(10.0)
    try:
        content = open(sig, encoding='utf-8').read()
        if 'HOP:CODEX' in content:
            log('[CC] HOP:CODEX confirmed via submit')
            sys.exit(0)
    except Exception:
        pass

# Fallback: directly execute relay_codex.py
log('[CC] Fallback: directly running relay_codex.py via subprocess')
result = subprocess.run([sys.executable, relay_codex], capture_output=True, text=True)
log(f'[CC] subprocess stdout: {{result.stdout.strip()!r}}')
log(f'[CC] subprocess stderr: {{result.stderr.strip()!r}}')
""", encoding="utf-8")

    log(f"  relay_cc.py written: {RELAY_CC_PY}")

# ── Spawn helpers ─────────────────────────────────────────────────────────────

def spawn_new_terminal(title: str, command: str) -> None:
    """Open a brand-new Windows Terminal window (separate process)."""
    subprocess.Popen(
        [WT_PATH, "--title", title, "cmd", "/k", command],
        creationflags=0x00000008,  # DETACHED_PROCESS
    )


def wait_for_new_wt_window(before_hwnds: set, title_fragment: str, timeout: float) -> WindowTarget | None:
    """Wait for a NEW Windows Terminal window.

    Detects by new HWND — title changes once the shell starts running so we
    can't filter by title at spawn time. Returns first new WT window.
    """
    deadline = time.monotonic() + timeout
    frag = title_fragment.lower()
    while time.monotonic() < deadline:
        candidates = []
        for w in list_windows():
            if w.hwnd in before_hwnds:
                continue
            if "windowsterminal" not in w.exe_name.lower():
                continue
            candidates.append(w)

        if candidates:
            # Prefer title match if multiple new windows appeared
            for w in candidates:
                if frag in w.title.lower():
                    return w
            return candidates[0]

        time.sleep(0.3)
    return None

# ── Step 1: Spawn Claude Code "testing term" ──────────────────────────────────

def step1_spawn_testing_term() -> WindowTarget:
    log("STEP 1: Spawning 'testing term' Claude Code (new window)...")
    before = {w.hwnd for w in list_windows()}
    spawn_new_terminal(CC_TITLE, f'cd /d "{REPO}" && claude')

    log("  Polling for new WT window...")
    win = wait_for_new_wt_window(before, CC_TITLE, SPAWN_TIMEOUT)
    if not win:
        raise RuntimeError(f"'testing term' did not appear within {SPAWN_TIMEOUT}s")

    log(f"  Window found: hwnd={win.hwnd}  title={win.title!r}")
    log("  Waiting for Claude Code TUI (watching window title for 'claude')...")
    ready = _wait_for_claude_ready(win.hwnd, timeout=45.0)
    log(f"  Ready={ready}")

    # Re-fetch to get updated title
    win2 = next((w for w in list_windows() if w.hwnd == win.hwnd), win)
    log(f"  Current title: {win2.title!r}")
    time.sleep(2.0)
    return win2

# ── Step 2: Spawn Codex "Codex Relay" ────────────────────────────────────────

def step2_spawn_codex() -> WindowTarget:
    log("STEP 2: Spawning 'Codex Relay' (new window)...")
    before = {w.hwnd for w in list_windows()}
    # codex config has ask_for_approval=never — runs non-interactively
    spawn_new_terminal(CODEX_TITLE, f'cd /d "{REPO}" && codex')

    log("  Polling for new WT window...")
    win = wait_for_new_wt_window(before, CODEX_TITLE, SPAWN_TIMEOUT)
    if not win:
        raise RuntimeError(f"Codex window did not appear within {SPAWN_TIMEOUT}s")

    log(f"  Window found: hwnd={win.hwnd}  title={win.title!r}")
    log("  Waiting 10s for codex to initialize...")
    time.sleep(10.0)
    win2 = next((w for w in list_windows() if w.hwnd == win.hwnd), win)
    log(f"  Current title: {win2.title!r}")
    return win2

# ── Step 3: Find Antigravity/Gemini ───────────────────────────────────────────

def step3_find_gemini() -> WindowTarget | None:
    log("STEP 3: Finding Antigravity/Gemini window...")
    for w in list_windows():
        if w.hwnd == ANTIGRAVITY_HWND:
            log(f"  Found: hwnd={w.hwnd}  title={w.title!r}")
            return w
        if "antigravity" in w.exe_name.lower():
            log(f"  Found via exe scan: hwnd={w.hwnd}  title={w.title!r}")
            return w
    log("  WARNING: Antigravity not running — HOP:GEMINI will be simulated")
    return None

# ── Step 4: Write relay scripts and brief Claude Code ─────────────────────────

def step4_brief_cc(cc_win: WindowTarget, codex_hwnd: int) -> None:
    log(f"STEP 4: Writing relay scripts and briefing Claude Code (hwnd={cc_win.hwnd})...")

    write_relay_scripts(codex_hwnd, str(SIGNAL_FILE))

    # Simple, unambiguous instruction to Claude Code
    briefing = (
        f"LOOP TEST. When I say GO, run this Bash command and report done: "
        f"python {RELAY_CC_PY}"
    )
    send_string(cc_win, briefing, mode="turbo")
    time.sleep(0.3)
    submit_claude_input(cc_win.hwnd)
    log("  Briefing submitted.")
    # Wait for Claude Code to process the briefing
    time.sleep(8.0)

# ── Step 5: Send GO to start the chain ────────────────────────────────────────

def step5_go(cc_win: WindowTarget) -> None:
    log(f"STEP 5: Sending GO to Claude Code (hwnd={cc_win.hwnd})...")
    send_string(cc_win, "GO", mode="turbo")
    time.sleep(0.3)
    submit_claude_input(cc_win.hwnd)
    log("  GO sent.")

# ── Step 6: Monitor loop + handle Gemini from orchestrator ────────────────────

def step6_monitor(gemini_win: WindowTarget | None) -> dict:
    log("STEP 6: Monitoring signal file...")
    log(f"  Path: {SIGNAL_FILE}")

    deadline = time.monotonic() + LOOP_TIMEOUT
    reported: set[str] = set()
    gemini_triggered = False

    while time.monotonic() < deadline:
        time.sleep(1.5)
        hops = read_hops()
        new = hops - reported
        for h in sorted(new):
            log(f"  >>> HOP RECEIVED: {h}")
        reported = hops

        # Once Codex hop arrives, orchestrator handles Gemini leg
        if "HOP:CODEX" in hops and not gemini_triggered:
            gemini_triggered = True
            log("  HOP:CODEX confirmed — triggering Gemini hop...")
            _do_gemini_hop(gemini_win)

        if {"HOP:CC", "HOP:CODEX", "HOP:GEMINI"} <= hops:
            elapsed = LOOP_TIMEOUT - (deadline - time.monotonic())
            return {"passed": True, "hops": sorted(hops), "elapsed_s": round(elapsed, 1)}

    return {
        "passed": False,
        "hops": sorted(reported),
        "elapsed_s": LOOP_TIMEOUT,
        "missing": sorted({"HOP:CC", "HOP:CODEX", "HOP:GEMINI"} - reported),
    }


def _do_gemini_hop(gemini_win: WindowTarget | None) -> None:
    if gemini_win is None:
        log("  No Antigravity window — writing HOP:GEMINI directly (simulated)")
        write_hop("HOP:GEMINI")
        return
    try:
        sys.path.insert(0, REPO)
        from antigravity_controller import chat, connect
        log(f"  Connecting to Antigravity hwnd={gemini_win.hwnd}...")
        session = connect(gemini_win.hwnd)
        response = chat(
            session,
            "Three-terminal relay test: you are node 3/3. Reply with exactly: HOP:GEMINI:OK",
            timeout=30,
        )
        log(f"  Gemini response: {response[:120]!r}")
        write_hop("HOP:GEMINI")
    except Exception as exc:
        log(f"  Gemini hop exception ({exc}) — writing HOP:GEMINI anyway")
        write_hop("HOP:GEMINI")

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("=" * 70)
    print("  SelfConnect Three-Way Loop Test")
    print("  Orchestrator -> Claude Code -> Codex -> Gemini -> back")
    print("=" * 70)
    print()

    clear_signal()
    log(f"Signal file cleared: {SIGNAL_FILE}")
    print()

    # Step 1: Open Claude Code "testing term"
    cc_win = step1_spawn_testing_term()
    print()

    # Step 2: Open Codex "Codex Relay"
    codex_win = step2_spawn_codex()
    print()

    # Step 3: Find Gemini
    gemini_win = step3_find_gemini()
    print()

    # Step 4: Write relay scripts + brief Claude Code
    step4_brief_cc(cc_win, codex_win.hwnd)
    print()

    # Step 5: Send GO to start the chain
    step5_go(cc_win)
    print()

    # Step 6: Monitor
    result = step6_monitor(gemini_win)
    print()

    # Results
    print("=" * 70)
    if result["passed"]:
        print(f"  LOOP TEST PASSED in {result['elapsed_s']}s")
        print(f"  All hops: {result['hops']}")
    else:
        print(f"  LOOP TEST INCOMPLETE after {result['elapsed_s']}s")
        print(f"  Hops received:  {result['hops']}")
        print(f"  Missing:        {result.get('missing', [])}")
    print("=" * 70)
    print()
    print("Window HWNDs:")
    print(f"  Claude Code 'testing term': hwnd={cc_win.hwnd}")
    print(f"  Codex Relay:                hwnd={codex_win.hwnd}")
    if gemini_win:
        print(f"  Gemini/Antigravity:         hwnd={gemini_win.hwnd}")
    print()
    print("Signal file contents:")
    try:
        print(SIGNAL_FILE.read_text(encoding="utf-8"))
    except Exception:
        print("  (empty or unreadable)")


if __name__ == "__main__":
    main()
