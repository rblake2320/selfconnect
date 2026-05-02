"""
hub_relay.py — Windows-A Hub Relay Loop
Polls Hub for messages from cc-spark2, executes CMD: commands on local Windows mesh,
posts results back to Hub.

Usage:
    python hub_relay.py              # run loop in foreground (Ctrl+C to stop)
    python hub_relay.py --once       # poll once and exit

CMD Protocol:
    CMD:MESH_STATUS              — full live state of all mesh peers
    CMD:CHECKPOINT               — read latest checkpoint.json
    CMD:list_windows()           — list all visible terminal windows
    CMD:capture_window(HWND)     — screenshot + text readback
    CMD:get_window_text(HWND)    — read text content of window
    CMD:send_string(LABEL, TEXT) — inject text into agent (LABEL=A/B/C/D/E)
    CMD:send_frame(LABEL, MSG)   — send framed protocol message to agent
    CMD:START_RELAY              — already running, confirm only
"""

import json
import sys
import time
import traceback
import urllib.request
import urllib.error
import os
import re

# Allow running from any cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import (
    list_windows, get_window_text, send_string, send_frame,
    capture_window, save_capture, find_target, WindowTarget,
    build_frame, parse_frame
)

HUB = "http://localhost:18765"  # SSH tunnel: ssh -L 18765:10.0.0.2:8765 spark1
FROM_AGENT = "windows-a"
REMOTE_AGENT = "cc-spark2"
POLL_INTERVAL = 30  # seconds

# HWND label map — update as session progresses
LABEL_MAP = {
    "A": 6756828,
    "B": 3546648,
    "C": 6624714,
    "D": 4854222,
    "E": 12585234,
}


def hub_get(path, timeout=8):
    url = HUB + path
    req = urllib.request.Request(url)
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())


def hub_post(path, payload, timeout=10):
    url = HUB + path
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())


def resolve_label(label_or_hwnd):
    """Return WindowTarget for a label (A/B/C/D/E) or numeric hwnd string."""
    label = label_or_hwnd.strip().upper()
    hwnd = LABEL_MAP.get(label)
    if hwnd is None:
        try:
            hwnd = int(label_or_hwnd.strip())
        except ValueError:
            return None
    wins = list_windows()
    return next((w for w in wins if w.hwnd == hwnd), None)


def poll_inbox():
    """Poll Hub for messages addressed to windows-a."""
    try:
        data = hub_get(f"/messages/poll/{FROM_AGENT}")
        return data.get("messages", [])
    except Exception as e:
        print(f"[relay] poll error: {e}")
        return []


def reply_to_hub(result_text, conversation_id=None):
    """Post a reply back to cc-spark2."""
    payload = {
        "from_agent": FROM_AGENT,
        "to_agent": REMOTE_AGENT,
        "content": result_text,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    try:
        r = hub_post("/messages/send_direct", payload)
        print(f"[relay] replied: {str(r)[:60]}")
    except Exception as e:
        print(f"[relay] reply error: {e}")


def cmd_mesh_status():
    """Return current mesh state."""
    wins = list_windows()
    lines = ["=== MESH STATUS (live) ==="]
    for label, hwnd in LABEL_MAP.items():
        w = next((x for x in wins if x.hwnd == hwnd), None)
        if w:
            title_ascii = w.title.encode("ascii", "replace").decode("ascii")
            lines.append(f"  {label} hwnd={hwnd} ONLINE  title={title_ascii[:50]}")
        else:
            lines.append(f"  {label} hwnd={hwnd} GONE")
    # Show any extra terminal windows
    known = set(LABEL_MAP.values())
    extras = [w for w in wins if w.hwnd not in known and w.exe_name == "WindowsTerminal.exe"]
    for w in extras:
        t = w.title.encode("ascii", "replace").decode("ascii")
        lines.append(f"  ?  hwnd={w.hwnd} ONLINE  title={t[:50]}")
    lines.append(f"SDK: v0.9.0 | relay: windows-a | remote: {REMOTE_AGENT}")
    return "\n".join(lines)


def cmd_checkpoint():
    """Read latest checkpoint from disk."""
    import glob
    base = os.path.dirname(os.path.abspath(__file__))
    paths = glob.glob(os.path.join(base, "proofs", "checkpoint_*.json"))
    if not paths:
        return "No checkpoint files found in proofs/"
    latest = max(paths, key=os.path.getmtime)
    with open(latest, encoding="utf-8") as f:
        data = json.load(f)
    return f"Checkpoint: {os.path.basename(latest)}\n{json.dumps(data, indent=2)}"


def cmd_list_windows():
    wins = list_windows()
    lines = [f"=== list_windows() — {len(wins)} windows ==="]
    for w in wins:
        t = w.title.encode("ascii", "replace").decode("ascii")
        lines.append(f"  hwnd={w.hwnd} exe={w.exe_name or '?'} title={t[:60]}")
    return "\n".join(lines)


def cmd_get_window_text(hwnd_or_label):
    w = resolve_label(hwnd_or_label)
    if not w:
        return f"ERROR: window not found for {hwnd_or_label!r}"
    text = get_window_text(w.hwnd)
    if not text:
        text = "(empty or unreadable)"
    return f"get_window_text({w.hwnd}):\n{text[-2000:]}"


def cmd_capture_window(hwnd_or_label):
    w = resolve_label(hwnd_or_label)
    if not w:
        return f"ERROR: window not found for {hwnd_or_label!r}"
    try:
        path = save_capture(w.hwnd)
        text = get_window_text(w.hwnd)
        return f"capture_window({w.hwnd}): saved to {path}\nText preview:\n{(text or '')[-1000:]}"
    except Exception as e:
        return f"capture_window({w.hwnd}) error: {e}"


def cmd_send_string(label, text):
    w = resolve_label(label)
    if not w:
        return f"ERROR: window not found for {label!r}"
    try:
        send_string(w, text + ("\r" if not text.endswith("\r") else ""))
        preview = repr(text[:50])
        return f"send_string({label}={w.hwnd}, {preview}) OK"
    except Exception as e:
        return f"send_string error: {e}"


def cmd_send_frame_msg(label, msg):
    w = resolve_label(label)
    if not w:
        return f"ERROR: window not found for {label!r}"
    try:
        send_frame(w, LABEL_MAP["A"], msg, topic="spark2-relay")
        return f"send_frame({label}={w.hwnd}) OK: {msg[:80]}"
    except Exception as e:
        return f"send_frame error: {e}"


def execute_cmd(content, conversation_id=None):
    """Parse and execute a CMD: command. Returns result string."""
    content = content.strip()
    if not content.startswith("CMD:"):
        return None  # not a command

    # Strip TAG: suffix (used by spark2_client.py for RPC reply matching)
    tag = ""
    tag_m = re.search(r"\s+TAG:(RPC-[a-f0-9]+)", content)
    if tag_m:
        tag = tag_m.group(1)
        content = content[:tag_m.start()]

    cmd = content[4:].strip()
    print(f"[relay] CMD: {cmd[:100]} tag={tag or 'none'}")

    def wrap(result):
        return f"{result}\nTAG:{tag}" if tag else result

    if cmd == "MESH_STATUS":
        return wrap(cmd_mesh_status())

    if cmd == "CHECKPOINT":
        return wrap(cmd_checkpoint())

    if cmd == "START_RELAY":
        return wrap("[relay] Relay loop is already running (30s poll). Processing your CMDs automatically.")

    if cmd.startswith("list_windows"):
        return wrap(cmd_list_windows())

    m = re.match(r"get_window_text\((.+)\)", cmd)
    if m:
        return wrap(cmd_get_window_text(m.group(1).strip()))

    m = re.match(r"capture_window\((.+)\)", cmd)
    if m:
        return wrap(cmd_capture_window(m.group(1).strip()))

    m = re.match(r"send_string\((\w+)\s*,\s*(.+)\)", cmd)
    if m:
        return wrap(cmd_send_string(m.group(1).strip(), m.group(2).strip()))

    m = re.match(r"send_frame\((\w+)\s*,\s*(.+)\)", cmd)
    if m:
        return wrap(cmd_send_frame_msg(m.group(1).strip(), m.group(2).strip()))

    return wrap(f"Unknown CMD: {cmd!r}. Supported: MESH_STATUS, CHECKPOINT, list_windows(), get_window_text(HWND), capture_window(HWND), send_string(LABEL,TEXT), send_frame(LABEL,MSG), START_RELAY")


def process_messages(messages):
    for msg in messages:
        from_a = msg.get("from_agent", "")
        content = msg.get("content", "")
        conv_id = msg.get("conversation_id")

        if from_a != REMOTE_AGENT:
            continue

        print(f"[relay] from={from_a} conv={str(conv_id)[:8] if conv_id else '?'} content={content[:80]!r}")

        result = execute_cmd(content, conv_id)
        if result is not None:
            reply_to_hub(result, conv_id)
        else:
            # Not a CMD: — echo back that we received it
            reply_to_hub(f"[windows-a] Received: {content[:200]}\nSend CMD: prefixed commands for mesh actions.", conv_id)


def main():
    once = "--once" in sys.argv
    print(f"[relay] Windows-A Hub relay starting. Hub={HUB} poll={POLL_INTERVAL}s")
    print(f"[relay] Listening for CMD: messages from {REMOTE_AGENT}")

    # Send an online notification
    try:
        reply_to_hub(
            f"[windows-a] Relay loop active. hwnd=6756828. "
            f"Mesh: D(4854222)=READY E(12585234)=READY B(3546648)=STALLED C(6624714)=STALLED. "
            f"Send CMD:MESH_STATUS to start."
        )
    except Exception as e:
        print(f"[relay] startup notify error: {e}")

    while True:
        try:
            msgs = poll_inbox()
            if msgs:
                print(f"[relay] {len(msgs)} message(s) in inbox")
                process_messages(msgs)
            else:
                print(f"[relay] inbox empty, sleeping {POLL_INTERVAL}s")
        except Exception as e:
            print(f"[relay] loop error: {e}")
            traceback.print_exc()

        if once:
            break
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
