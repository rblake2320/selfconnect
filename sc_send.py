"""Generic peer-send CLI — lets ANY shell-capable agent (Codex, Gemini, Claude)
send a message into another terminal via SelfConnect.

Usage:
    python sc_send.py --to <title-substring> <message words...>
    python sc_send.py --to "first contact" AXIOM: task complete, hash chain ok
    python sc_send.py --list                 # show injectable windows

Protocol enforced: text and Enter use one class-selected transport call.
Safety: refuses ambiguous targets, skips own window, idle-guards Codex-style
busy spinners unless --force.

Duplicate titles: tab names are project-scoped and reused across sessions, so
several visible windows can share one title (stale twins linger). On multiple
matches sc_send consults the mesh registry and picks the single match whose
hwnd carries an ACTIVE registration; anything less decisive stays AMBIGUOUS
(fail-closed). --first remains an explicit operator override, not a default.
"""
import argparse
import ctypes
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import get_text_uia, list_windows, send_string  # noqa: E402

BUSY_MARKERS = ("esc to interrupt", "Working (")


def own_hwnd():
    return ctypes.windll.kernel32.GetConsoleWindow()


def find_targets(sub):
    sub = sub.lower()
    me = own_hwnd()
    return [w for w in list_windows()
            if sub in (w.title or "").lower() and w.hwnd != me]


def registry_record_for(hwnd, registry):
    """Best registry record for an hwnd: an active one, else most recent."""
    candidates = [rec for rec in (registry or {}).get("agents", [])
                  if rec.get("hwnd") == hwnd]
    if not candidates:
        return None
    active = [rec for rec in candidates if rec.get("status") == "active"]
    pool = active or candidates
    return max(pool, key=lambda rec: rec.get("last_seen") or 0)


def pick_by_registry(matches, registry):
    """Resolve duplicate-title matches through mesh-registry classification.

    Returns (winner, rows). winner is the ONE match holding an active
    registration, or None when zero or several qualify — ambiguity between
    two live registered agents, rotated HWNDs (active record pointing at a
    window that no longer matches), and unreadable registries all stay
    fail-closed. rows carry (window, status, role, birth_id) for display.
    """
    rows = []
    active_matches = []
    for win in matches:
        rec = registry_record_for(win.hwnd, registry)
        status = rec.get("status") if rec else "unregistered"
        rows.append((win, status,
                     rec.get("role") if rec else None,
                     rec.get("birth_id") if rec else None))
        if rec and rec.get("status") == "active":
            active_matches.append(win)
    winner = active_matches[0] if len(active_matches) == 1 else None
    return winner, rows


def main():
    ap = argparse.ArgumentParser(description="Send a message to another terminal via SelfConnect")
    ap.add_argument("--to", help="target window title substring (case-insensitive)")
    ap.add_argument("--list", action="store_true", help="list candidate windows and exit")
    ap.add_argument("--first", action="store_true", help="on multiple matches, take the first")
    ap.add_argument("--force", action="store_true", help="skip the busy/idle guard")
    ap.add_argument("message", nargs="*", help="message words")
    args = ap.parse_args()

    if args.list:
        me = own_hwnd()
        for w in list_windows():
            if w.hwnd != me and w.title:
                print(f"0x{w.hwnd:08X}  {w.title[:70]}")
        return 0

    if not args.to or not args.message:
        ap.error("--to and a message are required (or use --list)")

    matches = find_targets(args.to)
    if not matches:
        print(f"NO MATCH for {args.to!r} — run with --list to see windows")
        return 1
    if len(matches) > 1 and not args.first:
        try:
            from sc_mesh_registry import load_registry
            registry = load_registry()
        except Exception:  # registry unreadable → behave exactly as before
            registry = None
        winner, rows = pick_by_registry(matches, registry)
        if winner is not None:
            _, status, role, birth = next(r for r in rows if r[0].hwnd == winner.hwnd)
            print(f"DISAMBIGUATED via mesh registry: role={role} birth={birth} "
                  f"0x{winner.hwnd:08X} (sole active registration among "
                  f"{len(matches)} title matches)")
            matches = [winner]
        else:
            print(f"AMBIGUOUS — {len(matches)} windows match {args.to!r} "
                  "and the mesh registry does not single one out "
                  "(use --first or narrow):")
            for w, status, role, _ in rows:
                extra = f"  [{status}" + (f": {role}]" if role else "]")
                print(f"  0x{w.hwnd:08X}  {w.title[:60]}{extra}")
            return 2
    win = matches[0]

    if not args.force:
        tail = (get_text_uia(win.hwnd) or "")[-800:]
        if any(m in tail for m in BUSY_MARKERS):
            print(f"BUSY — target 0x{win.hwnd:08X} is mid-task; retry later or --force")
            return 3

    msg = " ".join(args.message)
    delivery = send_string(win, msg + "\r", char_delay=0.02)
    if not isinstance(delivery, dict) or delivery.get("ok") is not True:
        transport = delivery.get("transport", "unknown") if isinstance(delivery, dict) else "unknown"
        error = delivery.get("error", "no delivery record") if isinstance(delivery, dict) else "no delivery record"
        print(f"FAILED via {transport}: {error}")
        return 4
    print(
        f"ACCEPTED {len(msg) + 1} chars via {delivery['transport']} -> "
        f"0x{win.hwnd:08X} {win.title[:50]!r}; consumption not verified"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
