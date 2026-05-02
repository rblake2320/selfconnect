"""
game_watchdog.py — keeps agent playing Dark War Survival
Runs from A's terminal. Every 60s: captures game, nudges F2 agent.
RULES enforced: no taskkill, no restart, only click_at + save_capture.
Lockfile: C:/Users/techai/tmp/watchdog.lock — prevents duplicate instances.
"""
import sys, os, time, atexit, ctypes, ctypes.wintypes
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import list_windows, send_string, save_capture, get_window_text

BS_HWND    = 220337560   # BlueStacks
AGENT_HWND = 378079626   # F2 agent terminal
INTERVAL   = 60          # seconds between watchdog checks
SHOT_DIR   = "C:/Users/techai/tmp"
LOCKFILE   = "C:/Users/techai/tmp/watchdog.lock"

user32 = ctypes.windll.user32


# ── Lockfile ──────────────────────────────────────────────────────────────────

def acquire_lock():
    """Ensure only one watchdog runs. Exits if another instance is active."""
    if os.path.exists(LOCKFILE):
        try:
            with open(LOCKFILE) as f:
                old_pid = int(f.read().strip())
            # Check if that PID is still alive
            import subprocess
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {old_pid}", "/FO", "CSV"],
                capture_output=True, text=True
            )
            if str(old_pid) in result.stdout:
                print(f"[watchdog] Another instance running (PID {old_pid}). Exiting.")
                sys.exit(1)
            else:
                print(f"[watchdog] Stale lock (PID {old_pid} gone). Overwriting.")
        except Exception:
            pass
    with open(LOCKFILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(release_lock)


def release_lock():
    try:
        os.remove(LOCKFILE)
    except Exception:
        pass


# ── Window helpers ────────────────────────────────────────────────────────────

def find_window(hwnd):
    wins = list_windows()
    return next((w for w in wins if w.hwnd == hwnd), None)


def nudge_agent(f2_win, game_shot_path, tick):
    msg = (
        f"[watchdog tick={tick}] Keep playing Dark War Survival. "
        f"Game screenshot saved: {game_shot_path} "
        f"BlueStacks screen rect: left=854 top=157 right=1660 bottom=1563. "
        f"Use save_capture(220337560, path) to read the game, "
        f"then click_at(screen_x, screen_y) to tap quest markers or buildings. "
        f"Rules: ONLY save_capture + click_at. NO taskkill. NO restart."
    )
    send_string(f2_win, msg + "\r")
    print(f"[watchdog] nudged agent tick={tick}")


def main():
    acquire_lock()
    print(f"[watchdog] Starting. PID={os.getpid()} BS={BS_HWND} Agent={AGENT_HWND} interval={INTERVAL}s")
    tick = 0

    while True:
        tick += 1
        time.sleep(INTERVAL)

        bs = find_window(BS_HWND)
        if not bs:
            print("[watchdog] BlueStacks GONE — stopping")
            break

        f2 = find_window(AGENT_HWND)
        if not f2:
            print("[watchdog] Agent terminal GONE — stopping")
            break

        shot_path = f"{SHOT_DIR}/watchdog_tick{tick}.png"
        try:
            save_capture(BS_HWND, shot_path)
            print(f"[watchdog] tick={tick} captured -> {shot_path}")
        except Exception as e:
            print(f"[watchdog] capture error: {e}")
            shot_path = "unknown"

        nudge_agent(f2, shot_path, tick)


if __name__ == "__main__":
    main()
