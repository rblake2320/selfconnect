"""Generic peer-send CLI — lets ANY shell-capable agent (Codex, Gemini, Claude)
send a message into another terminal via SelfConnect.

Usage:
    python sc_send.py --to <title-substring> <message words...>
    python sc_send.py --to "first contact" AXIOM: task complete, hash chain ok
    python sc_send.py --list                 # show injectable windows

Protocol enforced: text and Enter use one class-selected transport call.
Safety: refuses ambiguous targets (use --first to override), skips own window,
idle-guards Codex-style busy spinners unless --force.
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
        print(f"AMBIGUOUS — {len(matches)} windows match {args.to!r} (use --first or narrow):")
        for w in matches:
            print(f"  0x{w.hwnd:08X}  {w.title[:70]}")
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
