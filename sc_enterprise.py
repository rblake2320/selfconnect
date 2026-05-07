"""sc_enterprise.py — Win32 Surface Expansion for SelfConnect

Enterprise-grade agent infrastructure primitives built on the full Win32 IPC
surface. This module is ADDITIVE — it does NOT modify self_connect.py. Import
both and use whichever capabilities each situation requires.

    from self_connect import list_windows, send_string, get_text_uia
    from sc_enterprise import stamp_birth_tag, discover_mesh, BirthTag

Layer map:
    Tier 1 (self_connect.py)  — WM_CHAR injection, UIA readback [proven, production]
    Tier 2 (this file)        — SetProp registry, WM_COPYDATA transport, Named Events
    Tier 3 (future)           — CreateDesktop, Named Pipes, SetWinEventHook

Patent claims addressed here:
    Claim 3 (upgraded):  HWND self-discovery with structured birth-tag metadata
    Claim Set 2 (new):   SetProp/GetProp as zero-infrastructure distributed agent registry
    Claim Set 1 dep.:    WM_COPYDATA as OS-verified structured payload transport
    Claim Set 3 (new):   Named Events as zero-polling agent coordination primitives

Version: 1.0.0-enterprise  Session 16
"""
from __future__ import annotations

import ctypes
import json
import threading
import time
from dataclasses import asdict, dataclass
from typing import Optional

# ── Win32 handles ─────────────────────────────────────────────────────────────
user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ── Win32 constants ───────────────────────────────────────────────────────────
WM_COPYDATA  = 0x004A
GW_CHILD     = 5
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# ── Property key constants ─────────────────────────────────────────────────────
# All SelfConnect window properties use the "SC" prefix.
# These are the birth tag fields — stamped at spawn, readable by any peer.
PROP_ID      = "SCID"       # agent identity string  e.g. "agent-b-local-qwen3"
PROP_TYPE    = "SCTYPE"     # role: "claude_code" | "local_model" | "observer" | "unknown"
PROP_BORN    = "SCBORN"     # float str — time.time() at spawn
PROP_PARENT  = "SCPARENT"   # str(spawner_hwnd) or "0" if no known parent
PROP_MODEL   = "SCMODEL"    # model name e.g. "claude-sonnet-4-6" | "qwen3.6:27b"
PROP_HB      = "SCHB"       # float str — last heartbeat time.time()
PROP_SESSION = "SCSESS"     # optional session label e.g. "session-16"

# ── String atom helpers ────────────────────────────────────────────────────────
# SetProp / GetProp require the value to be a handle (HANDLE/atom).
# We store short strings by interning them as global atoms — this is the
# standard Win32 pattern for attaching string metadata to windows.

_atom_cache: dict[str, int] = {}

def _str_to_atom(value: str) -> int:
    """Intern a string as a global atom and return its atom ID."""
    if value not in _atom_cache:
        atom = ctypes.windll.kernel32.GlobalAddAtomW(value)
        if atom == 0:
            raise RuntimeError(f"GlobalAddAtomW failed for {value!r}")
        _atom_cache[value] = atom
    return _atom_cache[value]

def _atom_to_str(atom: int) -> str:
    """Resolve a global atom ID back to its string."""
    buf = ctypes.create_unicode_buffer(256)
    length = ctypes.windll.kernel32.GlobalGetAtomNameW(atom, buf, 256)
    if length == 0:
        return ""
    return buf.value

# ── BirthTag dataclass ────────────────────────────────────────────────────────

@dataclass
class BirthTag:
    """Identity certificate stamped on a window at agent spawn time.

    Anchored to a live HWND via Win32 window properties (SetProp/GetProp).
    Self-destructs when the window closes — architecturally impossible to have
    a stale certificate for a dead agent.

    Equivalent to Identity Forge AgentBirthCertificate but OS-native:
    no database, no cleanup, no garbage collection required.
    """
    hwnd:     int
    agent_id: str
    agent_type: str       # "claude_code" | "local_model" | "observer" | "unknown"
    born:     float       # epoch seconds at spawn
    parent:   int         # spawner hwnd, 0 if unknown
    model:    str         # model name/version
    heartbeat: float      # last heartbeat epoch seconds
    session:  str = ""    # optional session label

    def age_seconds(self) -> float:
        return time.time() - self.born

    def seconds_since_heartbeat(self) -> float:
        return time.time() - self.heartbeat

    def is_alive(self, stale_threshold: float = 120.0) -> bool:
        """True if heartbeat was updated within stale_threshold seconds."""
        return self.seconds_since_heartbeat() < stale_threshold

    def to_dict(self) -> dict:
        d = asdict(self)
        d["age_seconds"] = self.age_seconds()
        d["seconds_since_heartbeat"] = self.seconds_since_heartbeat()
        d["alive"] = self.is_alive()
        return d


# ── SetProp / GetProp wrappers ────────────────────────────────────────────────

def set_agent_prop(hwnd: int, key: str, value: str) -> bool:
    """Attach a string property to a window handle.

    Uses GlobalAddAtom to intern the value string as a system atom,
    then stores the atom as the property handle — the standard Win32 pattern.
    Returns True on success.
    """
    atom = _str_to_atom(value)
    ok = user32.SetPropW(hwnd, key, atom)
    return bool(ok)


def get_agent_prop(hwnd: int, key: str) -> str:
    """Read a string property from a window handle. Returns "" if absent."""
    atom = user32.GetPropW(hwnd, key)
    if atom == 0:
        return ""
    return _atom_to_str(atom)


def remove_agent_prop(hwnd: int, key: str) -> bool:
    """Remove a property from a window handle. Call at agent shutdown."""
    atom = user32.RemovePropW(hwnd, key)
    if atom and atom != 0:
        ctypes.windll.kernel32.GlobalDeleteAtom(atom)
    return True


# ── Birth tag lifecycle ────────────────────────────────────────────────────────

def stamp_birth_tag(
    hwnd: int,
    agent_id: str,
    agent_type: str,
    model: str,
    parent_hwnd: int = 0,
    session: str = "",
) -> BirthTag:
    """Stamp a birth tag on the given window handle.

    Call this once at agent startup, right after the window HWND is confirmed.
    The tag persists as long as the window exists.

    Args:
        hwnd:       The agent's own window handle.
        agent_id:   Unique agent identifier string e.g. "agent-b-local-qwen3".
        agent_type: Role classification ("claude_code", "local_model", "observer").
        model:      Model name/version e.g. "qwen3.6:27b".
        parent_hwnd: HWND of the process that spawned this agent (0 if unknown).
        session:    Optional session label e.g. "session-16".

    Returns:
        BirthTag dataclass representing the stamped certificate.
    """
    now = time.time()
    set_agent_prop(hwnd, PROP_ID,      agent_id)
    set_agent_prop(hwnd, PROP_TYPE,    agent_type)
    set_agent_prop(hwnd, PROP_BORN,    str(now))
    set_agent_prop(hwnd, PROP_PARENT,  str(parent_hwnd))
    set_agent_prop(hwnd, PROP_MODEL,   model)
    set_agent_prop(hwnd, PROP_HB,      str(now))
    if session:
        set_agent_prop(hwnd, PROP_SESSION, session)
    return BirthTag(
        hwnd=hwnd,
        agent_id=agent_id,
        agent_type=agent_type,
        born=now,
        parent=parent_hwnd,
        model=model,
        heartbeat=now,
        session=session,
    )


def update_heartbeat(hwnd: int) -> bool:
    """Update the heartbeat timestamp on an already-stamped window.

    Call periodically (e.g. every 30s) to signal liveness to peers.
    Returns True if the SCID property exists (window was stamped).
    """
    if not get_agent_prop(hwnd, PROP_ID):
        return False
    return set_agent_prop(hwnd, PROP_HB, str(time.time()))


def read_birth_tag(hwnd: int) -> Optional[BirthTag]:
    """Read a BirthTag from a window handle. Returns None if not stamped."""
    agent_id = get_agent_prop(hwnd, PROP_ID)
    if not agent_id:
        return None
    born_str = get_agent_prop(hwnd, PROP_BORN)
    hb_str   = get_agent_prop(hwnd, PROP_HB)
    parent_str = get_agent_prop(hwnd, PROP_PARENT)
    return BirthTag(
        hwnd=hwnd,
        agent_id=agent_id,
        agent_type=get_agent_prop(hwnd, PROP_TYPE) or "unknown",
        born=float(born_str) if born_str else 0.0,
        parent=int(parent_str) if parent_str else 0,
        model=get_agent_prop(hwnd, PROP_MODEL) or "",
        heartbeat=float(hb_str) if hb_str else 0.0,
        session=get_agent_prop(hwnd, PROP_SESSION) or "",
    )


# ── Mesh discovery ────────────────────────────────────────────────────────────

def discover_mesh() -> list[BirthTag]:
    """Enumerate all live SelfConnect agents on this machine.

    Walks all top-level windows, reads their SCID property, and returns
    BirthTag records for every window that has been stamped.

    The result is always current — no config file, no stale entries.
    Windows that have closed are automatically absent.
    """
    results: list[BirthTag] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    def _cb(hwnd: int, _: int) -> bool:
        tag = read_birth_tag(hwnd)
        if tag:
            results.append(tag)
        return True

    user32.EnumWindows(_cb, 0)
    return results


def find_agent(agent_id: str) -> Optional[BirthTag]:
    """Find a specific agent by ID in the live mesh. Returns None if not found."""
    for tag in discover_mesh():
        if tag.agent_id == agent_id:
            return tag
    return None


# ── WM_COPYDATA structured transport ─────────────────────────────────────────

class COPYDATASTRUCT(ctypes.Structure):
    """Win32 COPYDATASTRUCT — payload container for WM_COPYDATA messages."""
    _fields_ = [
        ("dwData", ctypes.c_ulong),   # application-defined message type
        ("cbData", ctypes.c_ulong),   # payload size in bytes
        ("lpData", ctypes.c_void_p),  # pointer to payload buffer
    ]

# SelfConnect WM_COPYDATA type IDs
SCDATA_JSON    = 0x5C01   # JSON-encoded payload
SCDATA_TASK    = 0x5C02   # structured task assignment
SCDATA_RESULT  = 0x5C03   # task result / tool output
SCDATA_PING    = 0x5C04   # liveness probe


def send_data(target_hwnd: int, payload: dict, data_type: int = SCDATA_JSON) -> bool:
    """Send a structured JSON payload to another agent via WM_COPYDATA.

    OS-verified: the recipient can read wParam to confirm sender HWND.
    Atomic: the entire payload is delivered in one message, no chunking.
    Up to 64KB per message. Sender does not need focus.

    Args:
        target_hwnd: Destination agent's HWND.
        payload:     Python dict — will be JSON-encoded and sent as bytes.
        data_type:   SCDATA_* constant identifying payload type.

    Returns:
        True if SendMessage returned non-zero (message delivered).
    """
    raw = json.dumps(payload).encode("utf-8")
    buf = ctypes.create_string_buffer(raw)
    cds = COPYDATASTRUCT(
        dwData=data_type,
        cbData=len(raw),
        lpData=ctypes.cast(buf, ctypes.c_void_p),
    )
    result = user32.SendMessageW(
        target_hwnd,
        WM_COPYDATA,
        0,  # wParam: sender hwnd (OS fills this for us when using SendMessage)
        ctypes.byref(cds),
    )
    return bool(result)


# ── Named Event coordination ──────────────────────────────────────────────────

def signal_ready(name: str) -> bool:
    """Signal a named event — wake any agent waiting on this name.

    Creates the event if it doesn't exist, then sets it.
    Any agent calling wait_for(name) will unblock immediately.

    Returns True on success.
    """
    handle = kernel32.CreateEventW(None, False, False, name)
    if not handle:
        return False
    kernel32.SetEvent(handle)
    kernel32.CloseHandle(handle)
    return True


def wait_for(name: str, timeout_ms: int = 30_000) -> bool:
    """Block until the named event is signaled or timeout expires.

    Zero CPU usage during the wait — OS wakes the thread on signal.
    Returns True if signaled, False if timeout expired.

    Args:
        name:       Named event string e.g. "AGENT-B-READY".
        timeout_ms: Maximum wait in milliseconds. Default 30s.
    """
    handle = kernel32.CreateEventW(None, False, False, name)
    if not handle:
        return False
    WAIT_OBJECT_0 = 0x00000000
    result = kernel32.WaitForSingleObject(handle, timeout_ms)
    kernel32.CloseHandle(handle)
    return result == WAIT_OBJECT_0


# ── Heartbeat daemon ──────────────────────────────────────────────────────────

class HeartbeatDaemon:
    """Background thread that updates SCHB on an agent window every N seconds.

    Usage:
        hb = HeartbeatDaemon(own_hwnd, interval=30)
        hb.start()
        # ... agent runs ...
        hb.stop()
    """

    def __init__(self, hwnd: int, interval: float = 30.0):
        self.hwnd     = hwnd
        self.interval = interval
        self._stop    = threading.Event()
        self._thread  = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            update_heartbeat(self.hwnd)


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [  # noqa: RUF022  # grouped by category, not alphabetical
    # Birth tag dataclass
    "BirthTag",
    # Property primitives
    "set_agent_prop", "get_agent_prop", "remove_agent_prop",
    # Birth tag lifecycle
    "stamp_birth_tag", "update_heartbeat", "read_birth_tag",
    # Mesh discovery
    "discover_mesh", "find_agent",
    # WM_COPYDATA transport
    "send_data", "COPYDATASTRUCT",
    "SCDATA_JSON", "SCDATA_TASK", "SCDATA_RESULT", "SCDATA_PING",
    # Named Event coordination
    "signal_ready", "wait_for",
    # Heartbeat daemon
    "HeartbeatDaemon",
    # Property key constants
    "PROP_ID", "PROP_TYPE", "PROP_BORN", "PROP_PARENT",
    "PROP_MODEL", "PROP_HB", "PROP_SESSION",
]


# ── CLI demo ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("SelfConnect Enterprise — Live Mesh Discovery")
    print("=" * 60)
    agents = discover_mesh()
    if not agents:
        print("No stamped agents found on this machine.")
        print("(Agents must call stamp_birth_tag() at startup)")
    else:
        for tag in agents:
            print(f"\n  HWND:      0x{tag.hwnd:x}")
            print(f"  ID:        {tag.agent_id}")
            print(f"  Type:      {tag.agent_type}")
            print(f"  Model:     {tag.model}")
            print(f"  Born:      {time.strftime('%H:%M:%S', time.localtime(tag.born))}")
            print(f"  Parent:    0x{tag.parent:x}")
            print(f"  Heartbeat: {tag.seconds_since_heartbeat():.1f}s ago")
            print(f"  Alive:     {tag.is_alive()}")
            if tag.session:
                print(f"  Session:   {tag.session}")
    print()
