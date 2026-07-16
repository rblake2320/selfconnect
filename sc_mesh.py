"""
sc_mesh — N-directional SelfConnect army coordinator.

One controller, many peers. Discovers all injectable agent terminals, classifies
them (claude/codex), and broadcasts or relays messages across the whole mesh.
Bidirectional, tridirectional, N-directional: every idle peer gets the message,
every reply is read back via UIA.

Commands:
    python sc_mesh.py roster                       # discover + classify all agents
    python sc_mesh.py broadcast <message...>       # send to ALL idle agents
    python sc_mesh.py broadcast --kind codex <msg> # send to one kind only
    python sc_mesh.py send 0xHWND <message...>     # one target
    python sc_mesh.py read 0xHWND                  # read a peer's last screen text
    python sc_mesh.py relay 0xSRC 0xDST            # copy SRC's last reply into DST

Protocol: text and Enter use one class-selected transport call. Skips self and BUSY peers
(unless --force). Broadcasts are staggered so windows don't interleave.
"""
import argparse
import ctypes
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import get_text_uia, list_windows, send_string  # noqa: E402

AGENT_CLASS = "CASCADIA_HOSTING_WINDOW_CLASS"
BUSY_MARKERS = ("esc to interrupt", "Working (")


def _own():
    return ctypes.windll.kernel32.GetConsoleWindow()


def classify(hwnd, title):
    tail = (get_text_uia(hwnd) or "")[-500:]
    t = (title or "").lower()
    if "gpt-5" in tail or "Codex" in tail or "codex" in t:
        kind = "codex"
    elif "auto mode" in tail or "Churned" in tail or "claude" in t:
        kind = "claude"
    else:
        kind = "unknown"
    busy = any(m in tail for m in BUSY_MARKERS)
    return kind, busy


def roster():
    me = _own()
    out = []
    for w in list_windows():
        if w.class_name != AGENT_CLASS or w.hwnd == me:
            continue
        kind, busy = classify(w.hwnd, w.title)
        out.append({"hwnd": w.hwnd, "win": w, "title": w.title or "",
                    "kind": kind, "busy": busy})
    return out


def _send(win, msg):
    delivery = send_string(win, msg + "\r", char_delay=0.02)
    if not isinstance(delivery, dict) or delivery.get("ok") is not True:
        transport = delivery.get("transport", "unknown") if isinstance(delivery, dict) else "unknown"
        error = delivery.get("error", "no delivery record") if isinstance(delivery, dict) else "no delivery record"
        raise RuntimeError(f"mesh input failed via {transport}: {error}")
    return delivery


def cmd_roster(_):
    for a in roster():
        state = "BUSY" if a["busy"] else "idle"
        print(f'0x{a["hwnd"]:08X}  [{a["kind"]:7} {state}]  {a["title"][:55]}')
    return 0


def cmd_broadcast(args):
    msg = " ".join(args.message)
    targets = [a for a in roster()
               if (args.kind in (None, a["kind"]))
               and (args.force or not a["busy"])]
    if not targets:
        print("no eligible idle targets")
        return 1
    print(f"broadcasting to {len(targets)} agents...")
    for a in targets:
        try:
            delivery = _send(a["win"], msg)
        except RuntimeError as exc:
            print(f'  !! 0x{a["hwnd"]:08X} [{a["kind"]}] {exc}')
            return 2
        print(
            f'  -> 0x{a["hwnd"]:08X} [{a["kind"]}] {a["title"][:40]} '
            f'via {delivery["transport"]}; consumption not verified'
        )
        time.sleep(2)  # stagger so windows don't interleave
    return 0


def _find(hwnd_hex):
    h = int(hwnd_hex, 16)
    return next((w for w in list_windows() if w.hwnd == h), None)


def cmd_send(args):
    win = _find(args.hwnd)
    if not win:
        print("target not found")
        return 1
    try:
        delivery = _send(win, " ".join(args.message))
    except RuntimeError as exc:
        print(f"failed -> 0x{win.hwnd:08X}: {exc}")
        return 2
    print(f"accepted -> 0x{win.hwnd:08X} via {delivery['transport']}; consumption not verified")
    return 0


def cmd_read(args):
    win = _find(args.hwnd)
    if not win:
        print("target not found")
        return 1
    print((get_text_uia(win.hwnd) or "")[-int(args.chars):])
    return 0


def cmd_relay(args):
    src = _find(args.src)
    dst = _find(args.dst)
    if not src or not dst:
        print("src or dst not found")
        return 1
    text = (get_text_uia(src.hwnd) or "").strip().splitlines()
    last = next((ln.strip() for ln in reversed(text) if ln.strip()), "")
    if not last:
        print("nothing to relay from src")
        return 1
    try:
        delivery = _send(dst, f"[relay from 0x{src.hwnd:08X}] {last}")
    except RuntimeError as exc:
        print(f"relay failed 0x{src.hwnd:08X} -> 0x{dst.hwnd:08X}: {exc}")
        return 2
    print(
        f"relayed 0x{src.hwnd:08X} -> 0x{dst.hwnd:08X} via {delivery['transport']}: "
        f"{last[:60]}; consumption not verified"
    )
    return 0


def main():
    ap = argparse.ArgumentParser(description="SelfConnect N-way mesh coordinator")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("roster").set_defaults(fn=cmd_roster)

    b = sub.add_parser("broadcast")
    b.add_argument("--kind", choices=["claude", "codex"])
    b.add_argument("--force", action="store_true")
    b.add_argument("message", nargs="+")
    b.set_defaults(fn=cmd_broadcast)

    s = sub.add_parser("send")
    s.add_argument("hwnd")
    s.add_argument("message", nargs="+")
    s.set_defaults(fn=cmd_send)

    r = sub.add_parser("read")
    r.add_argument("hwnd")
    r.add_argument("--chars", default="600")
    r.set_defaults(fn=cmd_read)

    rl = sub.add_parser("relay")
    rl.add_argument("src")
    rl.add_argument("dst")
    rl.set_defaults(fn=cmd_relay)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
