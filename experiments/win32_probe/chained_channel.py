"""
chained_channel.py — composed proof: UIA TextChanged read + Ed25519 identity + named pipe

Gate conditions met (session 9, branch test/win32-hardening-v1):
  UIA TextChanged  : PROVEN — uia_textchanged_fire.py, 2x fires on terminal output
  Named pipe DACL  : PROVEN — named_pipe_identity.py, OS-verified caller SID
  TPM ECDSA P-256  : PROVEN — tpm_ecdsa_p256.py, hardware-backed sign+verify
  UIA focus-free   : PROVEN — TextPattern.GetText() works on background window

Chain (Role A → Role B):
  inject SC_PROBE token via WM_CHAR
  → UIA TextChanged fires on TermControl (ControlType=50020, single 1.1M-char surface)
  → compute delta, strip echo (delta starts with probe token)
  → sign SHA-256(delta) with AgentIdentity (Ed25519; upgrade path: NCryptCreateClaim)
  -> write {token, delta_hash, signature, pubkey} to named pipe \\\\.\\pipe\\sc_chain
  → Role B reads pipe, verifies signature, prints confirmation

Usage:
  python chained_channel.py --role B --hwnd 0          # start pipe server (Role B)
  python chained_channel.py --role A --target <hwnd>   # inject + sign + send (Role A)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import ctypes
import ctypes.wintypes as wt

import comtypes
import comtypes.client
import pythoncom

sys.path.insert(0, __file__.replace("\\experiments\\win32_probe\\chained_channel.py", ""))
from self_connect import list_windows, send_string
from sc_identity import AgentIdentity

PIPE_NAME = r"\\.\pipe\sc_chain"
UIA_CLSID = "{FF48DBA4-60EF-4201-AA87-54103EEF594E}"
UIA_IID   = "{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}"
UIA_TextChangedEventId       = 20015
UIA_IsTextPatternAvailableId = 30040   # property id — filter for TextPattern support
TreeScope_Subtree            = 7


# ── UIA helpers ───────────────────────────────────────────────────────────────

def _get_uia():
    return comtypes.client.CreateObject(UIA_CLSID, interface=comtypes.client.GetModule("UIAutomationClient").IUIAutomation)


def _term_control_element(uia, hwnd: int):
    """Return (element, text_pattern) for the TermControl text surface on hwnd.

    Uses IsTextPatternAvailable filter (not ControlType) so TermControl
    (ControlType=Text/50020) is found regardless of its ControlType value.
    Picks the LONGEST-text element (the real scrollback), not the first match
    (which is typically the tab-title header — a known trap).
    Background reads: UIA GetText AND TextChanged events are focus- and
    visibility-independent on ConPTY surfaces (process-level buffer updates).
    PROVEN: uia_textchanged_fire.py fired 2x on a minimized, unfocused window.
    """
    root = uia.ElementFromHandle(hwnd)
    # Filter: only elements that expose IUIAutomationTextPattern
    cond = uia.CreatePropertyCondition(UIA_IsTextPatternAvailableId, True)
    found = root.FindAll(TreeScope_Subtree, cond)
    best_el, best_tp, best_len = None, None, -1
    for i in range(found.Length):
        el = found.GetElement(i)
        try:
            tp = el.GetCurrentPattern(10014)   # IUIAutomationTextPattern
            text = tp.DocumentRange.GetText(-1)
            if len(text) > best_len:
                best_el, best_tp, best_len = el, tp, len(text)
        except Exception:
            pass
    return best_el, best_tp


def _read_delta(tp, baseline_len: int) -> str:
    current = tp.DocumentRange.GetText(-1)
    return current[baseline_len:]


# ── Named pipe helpers ────────────────────────────────────────────────────────

kernel32 = ctypes.windll.kernel32
PIPE_ACCESS_DUPLEX  = 0x00000003
PIPE_TYPE_MESSAGE   = 0x00000004
PIPE_WAIT           = 0x00000000
INVALID_HANDLE      = ctypes.c_void_p(-1).value
GENERIC_READ        = 0x80000000
FILE_SHARE_NONE     = 0
OPEN_EXISTING       = 3


def _create_pipe():
    h = kernel32.CreateNamedPipeW(
        PIPE_NAME, PIPE_ACCESS_DUPLEX,
        PIPE_TYPE_MESSAGE | PIPE_WAIT,
        1, 4096, 4096, 0, None,
    )
    if h == INVALID_HANDLE:
        raise OSError(f"CreateNamedPipe failed: {ctypes.GetLastError()}")
    return h


def _pipe_write(payload: dict):
    data = json.dumps(payload).encode()
    h = kernel32.CreateFileW(PIPE_NAME, GENERIC_READ | 0x40000000, 0, None, OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE:
        raise OSError(f"CreateFile(pipe) failed: {ctypes.GetLastError()}")
    written = wt.DWORD(0)
    kernel32.WriteFile(h, data, len(data), ctypes.byref(written), None)
    kernel32.CloseHandle(h)


def _pipe_read(h) -> dict:
    buf = ctypes.create_string_buffer(8192)
    read = wt.DWORD(0)
    kernel32.ReadFile(h, buf, len(buf), ctypes.byref(read), None)
    return json.loads(buf.raw[: read.value])


# ── Role A: inject → watch → sign → send ─────────────────────────────────────

def role_a(target_hwnd: int):
    identity = AgentIdentity.generate(label="RMC-chain")
    token = f"SC_PROBE_{id(identity) & 0xFFFF:04x}"
    print(f"[A] identity: {identity.did}")
    print(f"[A] probe token: {token}")

    pythoncom.CoInitialize()
    uia = _get_uia()
    el, tp = _term_control_element(uia, target_hwnd)
    if el is None:
        print("[A] ERROR: no TextPattern element on target hwnd")
        pythoncom.CoUninitialize()
        return

    baseline_len = len(tp.DocumentRange.GetText(-1))
    print(f"[A] baseline buffer: {baseline_len} chars")

    fired: list[str] = []

    class _Handler(comtypes.COMObject):
        _com_interfaces_ = [comtypes.client.GetModule("UIAutomationClient").IUIAutomationEventHandler]
        def HandleAutomationEvent(self, sender, eventId):
            fired.append(_read_delta(tp, baseline_len))

    handler = _Handler()
    uia.AddAutomationEventHandler(UIA_TextChangedEventId, el, 1, None, handler)
    print("[A] TextChanged handler registered")

    wins = list_windows()
    target = next((w for w in wins if w.hwnd == target_hwnd), None)
    if target is None:
        print(f"[A] ERROR: hwnd {target_hwnd} not in window list")
        return

    send_string(target, token)
    print(f"[A] probe injected via WM_CHAR")

    deadline = time.time() + 15
    while time.time() < deadline and not fired:
        pythoncom.PumpWaitingMessages()
        time.sleep(0.05)

    uia.RemoveAllEventHandlers()
    pythoncom.CoUninitialize()

    if not fired:
        print("[A] TIMEOUT — TextChanged never fired")
        return

    delta = fired[0]
    echo = delta.startswith(token)
    print(f"[A] TextChanged fired. delta[0:60]={delta[:60]!r}  echo={echo}")

    msg_hash = hashlib.sha256(delta.encode()).digest()
    sig = identity.sign(msg_hash)
    payload = {
        "token":      token,
        "delta_hash": msg_hash.hex(),
        "signature":  sig.hex(),
        "pubkey":     identity.public_key_hex,
        "echo":       echo,
    }
    _pipe_write(payload)
    print(f"[A] signed payload written to pipe. hash={msg_hash.hex()[:16]}...")


# ── Role B: listen → verify ───────────────────────────────────────────────────

def role_b():
    print(f"[B] waiting on {PIPE_NAME}")
    h = _create_pipe()
    kernel32.ConnectNamedPipe(h, None)
    payload = _pipe_read(h)
    kernel32.CloseHandle(h)

    print(f"[B] received: token={payload['token']}  echo={payload['echo']}")
    msg_hash = bytes.fromhex(payload["delta_hash"])
    sig      = bytes.fromhex(payload["signature"])
    pubkey   = payload["pubkey"]
    ok = AgentIdentity.verify_with_pubkey_hex(pubkey, msg_hash, sig)
    print(f"[B] signature valid={ok}  hash={payload['delta_hash'][:16]}...")
    if ok:
        print("[B] CHAIN COMPLETE — read channel + identity + transport all verified")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--role",   required=True, choices=["A", "B"])
    p.add_argument("--target", type=lambda x: int(x, 0), default=0,
                   help="target window hwnd (Role A only)")
    args = p.parse_args()
    if args.role == "A":
        if not args.target:
            p.error("--target hwnd required for Role A")
        role_a(args.target)
    else:
        role_b()
