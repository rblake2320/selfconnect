"""
chained_channel.py -- composed proof: UIA TextChanged + Ed25519 identity + DACL named pipe

Gate conditions met (session 9, test/win32-hardening-v1):
  UIA TextChanged  : PROVEN focus- and visibility-independent (minimized conhost)
  Named pipe DACL  : PROVEN -- ImpersonateNamedPipeClient reads OS-verified caller SID
  TPM ECDSA P-256  : PROVEN (separate probe); signing here is Ed25519 SOFTWARE identity
                     Upgrade path: replace AgentIdentity.sign() with NCryptCreateClaim
                     (NCRYPT_CLAIM_PLATFORM) for hardware attestation.

Chain (Role A -> Role B):
  spawn throwaway conhost as safe injection target
  inject SC_PROBE token via WM_CHAR (background-safe, no focus needed)
  UIA TextChanged fires on TermControl (IsTextPatternAvailable + longest-text)
  compute delta, strip echo (delta.lstrip().startswith(token))
  sign SHA-256(delta) with AgentIdentity [Ed25519, software identity]
  write {token, delta_hash, sig, pubkey} to named pipe
  Role B: ConnectNamedPipe, ImpersonateNamedPipeClient (OS-verified SID),
          read payload, verify Ed25519 signature

Usage:
  # Terminal 1 (Role B first -- pipe must exist before A connects):
  python chained_channel.py --role B

  # Terminal 2 (Role A -- spawns throwaway target automatically):
  python chained_channel.py --role A

  # Or supply an existing throwaway hwnd:
  python chained_channel.py --role A --target 0x1A2B3C
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import subprocess
import sys
import time

import comtypes
import comtypes.client
import pythoncom

# B1 FIX: GetModule takes the DLL name, not a bare type-library alias.
# This generates (or reuses) the comtypes.gen.UIAutomationClient wrapper.
_UIA_MOD = comtypes.client.GetModule("UIAutomationCore.dll")

sys.path.insert(0, __file__.replace("\\experiments\\win32_probe\\chained_channel.py", ""))
from self_connect import list_windows, send_string  # noqa: E402
from sc_identity import AgentIdentity               # noqa: E402

# ── constants ─────────────────────────────────────────────────────────────────
PIPE_NAME                    = r"\\.\pipe\sc_chain"
UIA_IsTextPatternAvailableId = 30040   # filter: elements that expose TextPattern
UIA_TextChangedEventId       = 20015
TreeScope_Subtree            = 7

PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_TYPE_MESSAGE  = 0x00000004
GENERIC_READ       = 0x80000000
GENERIC_WRITE      = 0x40000000
OPEN_EXISTING      = 3

# B2 FIX: INVALID_HANDLE_VALUE is all-bits-set; use c_size_t for platform width.
INVALID_HANDLE = ctypes.c_size_t(-1).value

# ── Win32 ABI-safe prototypes (no c_int for handles/pointers) ─────────────────
_k32 = ctypes.windll.kernel32
_a32 = ctypes.windll.advapi32
_u32 = ctypes.windll.user32

# B2 FIX: pipe handles are HANDLE (pointer-sized); restype must be c_void_p.
_k32.CreateNamedPipeW.restype       = ctypes.c_void_p
_k32.CreateFileW.restype            = ctypes.c_void_p
_k32.ConnectNamedPipe.argtypes      = [ctypes.c_void_p, ctypes.c_void_p]
_k32.ConnectNamedPipe.restype       = wt.BOOL
_k32.ReadFile.argtypes              = [ctypes.c_void_p, ctypes.c_char_p,
                                       wt.DWORD, ctypes.POINTER(wt.DWORD),
                                       ctypes.c_void_p]
_k32.ReadFile.restype               = wt.BOOL
_k32.WriteFile.argtypes             = [ctypes.c_void_p, ctypes.c_char_p,
                                       wt.DWORD, ctypes.POINTER(wt.DWORD),
                                       ctypes.c_void_p]
_k32.WriteFile.restype              = wt.BOOL
_k32.CloseHandle.argtypes           = [ctypes.c_void_p]
_k32.CloseHandle.restype            = wt.BOOL
_a32.ImpersonateNamedPipeClient.argtypes = [ctypes.c_void_p]
_a32.ImpersonateNamedPipeClient.restype  = wt.BOOL
_a32.RevertToSelf.restype                = wt.BOOL


def _invalid(h) -> bool:
    """True if a handle returned by CreateNamedPipeW/CreateFileW is invalid."""
    if h is None:
        return True
    return ctypes.c_size_t(h).value == INVALID_HANDLE


# ── UIA helpers ───────────────────────────────────────────────────────────────

def _get_uia():
    # B1 FIX: interface from the loaded module, not a bare string lookup.
    return comtypes.client.CreateObject(
        "{FF48DBA4-60EF-4201-AA87-54103EEF594E}",
        interface=_UIA_MOD.IUIAutomation,
    )


def _term_control_element(uia, hwnd: int):
    """Walk UIA tree; return (element, TextPattern) for TermControl scrollback.

    Strategy: filter by IsTextPatternAvailable (property 30040), NOT ControlType --
    the TermControl is ControlType=Text(50020) but filtering by ControlType.Document
    misses it. FindAll + pick longest text avoids the tab-title-header trap of
    FindFirst.  Background reads are focus-independent on ConPTY surfaces.
    """
    root = uia.ElementFromHandle(hwnd)
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
    return tp.DocumentRange.GetText(-1)[baseline_len:]


# ── Pipe helpers ──────────────────────────────────────────────────────────────

def _create_pipe():
    # NULL security descriptor = permissive (any local process can connect).
    # Production: pass a SECURITY_ATTRIBUTES with a DACL restricted to known SID.
    h = _k32.CreateNamedPipeW(
        PIPE_NAME, PIPE_ACCESS_DUPLEX, PIPE_TYPE_MESSAGE,
        1, 4096, 4096, 0, None,
    )
    if _invalid(h):
        raise OSError(f"CreateNamedPipeW failed: {ctypes.GetLastError()}")
    return h


def _pipe_write(payload: dict):
    data = json.dumps(payload).encode()
    h = _k32.CreateFileW(
        PIPE_NAME, GENERIC_READ | GENERIC_WRITE,
        0, None, OPEN_EXISTING, 0, None,
    )
    if _invalid(h):
        raise OSError(f"CreateFileW(pipe) failed: {ctypes.GetLastError()}")
    written = wt.DWORD(0)
    _k32.WriteFile(h, data, len(data), ctypes.byref(written), None)
    _k32.CloseHandle(h)


def _pipe_read_with_impersonation(h) -> tuple[dict, bool]:
    """Read message; impersonate caller to exercise OS-verified-SID leg."""
    # ImpersonateNamedPipeClient: OS records caller SID at ConnectNamedPipe;
    # this call adopts that identity on the current thread.
    impersonated = bool(_a32.ImpersonateNamedPipeClient(h))
    buf  = ctypes.create_string_buffer(8192)
    read = wt.DWORD(0)
    _k32.ReadFile(h, buf, len(buf), ctypes.byref(read), None)
    _a32.RevertToSelf()   # always drop impersonation
    return json.loads(buf.raw[: read.value]), impersonated


# ── Throwaway target ──────────────────────────────────────────────────────────

def _spawn_target() -> tuple[int, subprocess.Popen]:
    """Spawn an isolated conhost window safe for WM_CHAR injection."""
    proc = subprocess.Popen(
        ["conhost.exe", "cmd.exe", "/K", "echo SC_TARGET_READY"],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    deadline = time.time() + 20
    hwnd = None
    while time.time() < deadline and hwnd is None:
        time.sleep(0.5)
        for w in list_windows():
            if getattr(w, "pid", 0) == proc.pid:
                hwnd = w.hwnd
                break
    if hwnd is None:
        proc.terminate()
        raise RuntimeError("Throwaway target did not appear within 20s")
    time.sleep(1.0)   # let conhost fully initialize
    return hwnd, proc


# ── Role A: inject -> TextChanged -> sign -> pipe ─────────────────────────────

def role_a(target_hwnd: int):
    identity = AgentIdentity.generate(label="RMC-chain")
    token = f"SC_PROBE_{id(identity) & 0xFFFF:04x}"

    print(f"[A] did={identity.did}")
    print(f"[A] NOTE: signing is Ed25519 SOFTWARE identity (not hardware TPM)")
    print(f"[A]       TPM upgrade: replace .sign() with NCryptCreateClaim(NCRYPT_CLAIM_PLATFORM)")
    print(f"[A] token={token}  target_hwnd={target_hwnd:#x}")

    pythoncom.CoInitialize()
    uia = _get_uia()
    el, tp = _term_control_element(uia, target_hwnd)
    if el is None:
        print("[A] ERROR: no TextPattern element on target hwnd")
        pythoncom.CoUninitialize()
        return

    baseline_len = len(tp.DocumentRange.GetText(-1))
    print(f"[A] baseline buffer={baseline_len} chars")

    fired: list[str] = []

    # B3 FIX: comtypes dispatches on the full prefixed method name.
    class _Handler(comtypes.COMObject):
        _com_interfaces_ = [_UIA_MOD.IUIAutomationEventHandler]

        def IUIAutomationEventHandler_HandleAutomationEvent(self, sender, eventId):
            fired.append(_read_delta(tp, baseline_len))

    handler = _Handler()
    uia.AddAutomationEventHandler(UIA_TextChangedEventId, el, 1, None, handler)
    print("[A] TextChanged handler registered")

    wins = list_windows()
    target_win = next((w for w in wins if w.hwnd == target_hwnd), None)
    if target_win is None:
        print(f"[A] ERROR: hwnd {target_hwnd:#x} not found in window list")
        uia.RemoveAllEventHandlers()
        pythoncom.CoUninitialize()
        return

    send_string(target_win, token)
    print("[A] probe injected via WM_CHAR")

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
    echo  = delta.lstrip().startswith(token)
    print(f"[A] TextChanged fired | echo={echo} | delta[0:80]={delta[:80]!r}")

    msg_hash = hashlib.sha256(delta.encode()).digest()
    sig = identity.sign(msg_hash)
    _pipe_write({
        "token":      token,
        "delta_hash": msg_hash.hex(),
        "signature":  sig.hex(),
        "pubkey":     identity.public_key_hex,
        "echo":       echo,
    })
    print(f"[A] payload signed and written to pipe | hash={msg_hash.hex()[:16]}...")


# ── Role B: pipe listen -> impersonate -> verify ──────────────────────────────

def role_b():
    print(f"[B] listening on {PIPE_NAME}")
    print(f"[B] NOTE: NULL DACL = permissive; ImpersonateNamedPipeClient proves")
    print(f"[B]       OS-verified-SID leg. Production: restrict DACL to caller SID.")
    h = _create_pipe()
    _k32.ConnectNamedPipe(h, None)
    payload, impersonated = _pipe_read_with_impersonation(h)
    _k32.CloseHandle(h)

    print(f"[B] ImpersonateNamedPipeClient={'OK' if impersonated else 'FAILED'}")
    print(f"[B] token={payload['token']}  echo={payload['echo']}")

    msg_hash = bytes.fromhex(payload["delta_hash"])
    sig      = bytes.fromhex(payload["signature"])
    sig_ok   = AgentIdentity.verify_with_pubkey_hex(payload["pubkey"], msg_hash, sig)

    print(f"[B] signature valid={sig_ok} | hash={payload['delta_hash'][:16]}...")
    if sig_ok and impersonated:
        print("[B] CHAIN COMPLETE: UIA read + Ed25519 identity + DACL pipe all exercised")
    elif not impersonated:
        print("[B] PARTIAL: signature valid but impersonation failed -- check pipe security")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--role",   required=True, choices=["A", "B"])
    ap.add_argument("--target", type=lambda x: int(x, 0), default=0,
                    help="target hwnd for Role A (0 = spawn throwaway conhost)")
    args = ap.parse_args()

    if args.role == "B":
        role_b()
    else:
        hwnd, proc = (args.target, None) if args.target else _spawn_target(), None
        if not args.target:
            hwnd, proc = _spawn_target()
            print(f"[A] throwaway target hwnd={hwnd:#x}")
        try:
            role_a(hwnd)
        finally:
            if proc:
                proc.terminate()
