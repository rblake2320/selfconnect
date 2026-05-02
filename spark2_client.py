"""
spark2_client.py — SelfConnect Remote Client for Linux (Spark-2)
Mirrors the self_connect.py API but tunnels every call through the Hub relay.

Usage on Spark-2:
    git clone https://github.com/rblake2320/selfconnect
    cd selfconnect
    python3 spark2_client.py          # interactive demo

Or import as a library:
    from spark2_client import SC
    sc = SC()                         # connects to Hub
    windows = sc.list_windows()       # real data from Windows-A
    sc.send_string("D", "hello")      # injects into agent D on Windows
    text = sc.get_window_text(4854222)
    print(text)

Hub URL: http://10.0.0.2:8765  (Spark-2 can hit this directly)
"""

import json
import time
import uuid
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Optional

HUB = "http://10.0.0.2:8765"
ME = "cc-spark2"
TARGET = "windows-a"
REPLY_TIMEOUT = 45   # seconds to wait for Windows-A reply
POLL_INTERVAL = 2    # seconds between polls


# ── Data classes that mirror self_connect.py ──────────────────────────────────

@dataclass
class WindowTarget:
    hwnd: int
    title: str
    exe_name: str
    pid: int = 0


# ── Transport layer ───────────────────────────────────────────────────────────

class HubTransport:
    """Low-level Hub API calls."""

    def __init__(self, hub=HUB, me=ME, target=TARGET):
        self.hub = hub
        self.me = me
        self.target = target

    def _post(self, path, payload, timeout=10):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.hub + path, data=data,
            headers={"Content-Type": "application/json"}
        )
        r = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(r.read())

    def _get(self, path, timeout=8):
        r = urllib.request.urlopen(self.hub + path, timeout=timeout)
        return json.loads(r.read())

    def send(self, content, conversation_id=None):
        payload = {"from_agent": self.me, "to_agent": self.target, "content": content}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        return self._post("/messages/send_direct", payload)

    def poll_inbox(self):
        d = self._get(f"/messages/poll/{self.me}")
        return d.get("messages", [])

    def health(self):
        return self._get("/health")


# ── RPC call layer ────────────────────────────────────────────────────────────

class SC:
    """
    SelfConnect remote client.  API mirrors self_connect.py.
    Every method sends a CMD: to Windows-A and waits for the JSON reply.
    """

    def __init__(self, hub=HUB, me=ME, target=TARGET, timeout=REPLY_TIMEOUT):
        self.transport = HubTransport(hub, me, target)
        self.timeout = timeout
        self._tag_counter = 0

    # ── internal RPC ─────────────────────────────────────────────────────────

    def _call(self, cmd_str):
        """Send a CMD: to Windows-A, wait for reply, return reply content."""
        tag = f"RPC-{uuid.uuid4().hex[:8]}"
        self.transport.send(f"CMD:{cmd_str} TAG:{tag}")

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL)
            msgs = self.transport.poll_inbox()
            for m in msgs:
                if m.get("from_agent") == self.transport.target:
                    content = m.get("content", "")
                    if tag in content or not tag:
                        return content
        raise TimeoutError(f"No reply from {self.transport.target} within {self.timeout}s")

    def _call_json(self, cmd_str):
        """Like _call but tries to parse JSON from the reply."""
        raw = self._call(cmd_str)
        # Try to find a JSON block in the reply
        try:
            # Reply may start with text then JSON
            start = raw.find("{")
            if start != -1:
                return json.loads(raw[start:])
        except Exception:
            pass
        return {"raw": raw}

    # ── Public API (mirrors self_connect.py) ──────────────────────────────────

    def list_windows(self) -> list:
        """Return list of WindowTarget visible on the Windows machine."""
        raw = self._call("list_windows()")
        windows = []
        for line in raw.splitlines():
            line = line.strip()
            if "hwnd=" not in line:
                continue
            try:
                parts = {}
                for token in line.split():
                    if "=" in token:
                        k, v = token.split("=", 1)
                        parts[k] = v
                hwnd = int(parts.get("hwnd", 0))
                exe = parts.get("exe", "")
                # Title is the rest after exe=...
                title_start = line.find("title=")
                title = line[title_start + 6:] if title_start != -1 else ""
                if hwnd:
                    windows.append(WindowTarget(hwnd=hwnd, title=title, exe_name=exe))
            except Exception:
                continue
        return windows

    def get_window_text(self, hwnd_or_label) -> str:
        """Read the current text content of a window."""
        return self._call(f"get_window_text({hwnd_or_label})")

    def capture_window(self, hwnd_or_label) -> str:
        """Screenshot a window. Returns text description + path."""
        return self._call(f"capture_window({hwnd_or_label})")

    def send_string(self, label: str, text: str) -> str:
        """Inject text into an agent's terminal window."""
        return self._call(f"send_string({label},{text})")

    def send_frame(self, label: str, message: str) -> str:
        """Send a framed protocol message to an agent."""
        return self._call(f"send_frame({label},{message})")

    def mesh_status(self) -> str:
        """Get live state of all mesh peers."""
        return self._call("MESH_STATUS")

    def checkpoint(self) -> str:
        """Read the latest checkpoint.json from disk."""
        return self._call("CHECKPOINT")

    def health(self) -> dict:
        """Check Hub health."""
        return self.transport.health()

    # ── Convenience ──────────────────────────────────────────────────────────

    def __repr__(self):
        return f"SC(hub={self.transport.hub}, me={self.transport.me}, target={self.transport.target})"


# ── Interactive demo ──────────────────────────────────────────────────────────

def main():
    print("SelfConnect Remote Client — Spark-2")
    print(f"Hub: {HUB}")
    print()

    sc = SC()

    try:
        h = sc.health()
        print(f"Hub health: {h.get('status','?')} agents_online={h.get('stats',{}).get('agents_online','?')}")
    except Exception as e:
        print(f"Hub unreachable: {e}")
        return

    print("\nFetching mesh status from Windows-A...")
    try:
        status = sc.mesh_status()
        print(status)
    except TimeoutError as e:
        print(f"Timeout: {e}")
        print("Make sure hub_relay.py is running on Windows-A.")
        return

    print("\nListing windows...")
    try:
        wins = sc.list_windows()
        print(f"Found {len(wins)} windows:")
        for w in wins:
            print(f"  hwnd={w.hwnd} exe={w.exe_name} title={w.title[:60]}")
    except TimeoutError as e:
        print(f"Timeout: {e}")

    print("\nDone. Import SC from spark2_client and call sc.send_frame('D', 'hello') etc.")


if __name__ == "__main__":
    main()
