"""sc_session.py — Agent-side session identity helpers for SelfConnect.

Usage pattern:
    from sc_session import get_my_hwnd, verify_peer, bootstrap_from_briefing

    # Called when the agent receives its first SC_BOOTSTRAP message:
    bootstrap_from_briefing("SC_BOOTSTRAP: your_hwnd=0x00490d60 your_scid=claude-code-spawn-...")

    # Called before trusting any message from a peer:
    ok, tag = verify_peer(claimed_hwnd)
    if not ok:
        raise RuntimeError(f"Peer at 0x{claimed_hwnd:08x} failed identity verification")
"""
from __future__ import annotations

import os
import sys
import re
import json
from pathlib import Path
from typing import Optional, Tuple

_SESSIONS_DIR = Path(__file__).parent / '.sessions'
_SESSION_FILE  = _SESSIONS_DIR / 'current.json'

_ENT_SDK = Path(__file__).parent.parent / 'selfconnect-enterprise' / 'sdk'
sys.path.insert(0, str(_ENT_SDK))

try:
    from enterprise.registry import read_birth_tag, verify_tag, BirthTag
    _REGISTRY_AVAILABLE = True
except ImportError:
    _REGISTRY_AVAILABLE = False


# ── Session state (in-memory + persisted to .sessions/current.json) ───────────

_session: dict = {}


def _save() -> None:
    _SESSIONS_DIR.mkdir(exist_ok=True)
    _SESSION_FILE.write_text(json.dumps(_session, indent=2))


def _load() -> None:
    global _session
    if _SESSION_FILE.exists():
        try:
            _session = json.loads(_SESSION_FILE.read_text())
        except Exception:
            _session = {}


_load()


# ── Public API ─────────────────────────────────────────────────────────────────

def bootstrap_from_briefing(text: str) -> Optional[int]:
    """Parse an SC_BOOTSTRAP line and store session identity.

    Expected format (injected by _spawn_claude.py):
        SC_BOOTSTRAP: your_hwnd=0x00490d60 your_scid=claude-code-spawn-00490d60

    Returns the HWND as int, or None if pattern not found.
    """
    m = re.search(r'your_hwnd=0x([0-9a-fA-F]+)', text)
    if not m:
        return None
    hwnd = int(m.group(1), 16)
    scid_m = re.search(r'your_scid=(\S+)', text)
    scid = scid_m.group(1) if scid_m else f'unknown-0x{hwnd:08x}'

    _session['my_hwnd'] = hwnd
    _session['my_scid'] = scid
    _save()
    return hwnd


def set_my_hwnd(hwnd: int, scid: str = '') -> None:
    """Manually record this session's HWND (call if bootstrap line was missed)."""
    _session['my_hwnd'] = hwnd
    if scid:
        _session['my_scid'] = scid
    _save()


def get_my_hwnd() -> Optional[int]:
    """Return this agent's known Windows Terminal HWND, or None if not bootstrapped."""
    return _session.get('my_hwnd')


def get_my_scid() -> Optional[str]:
    """Return this agent's SCID, or None if not bootstrapped."""
    return _session.get('my_scid')


def identity_string() -> str:
    """Return a string the agent can include in outbound messages for peer verification.

    Example output:
        "SC_ID: hwnd=0x00490d60 scid=claude-code-spawn-00490d60"

    The peer calls verify_peer(0x00490d60) to validate.
    """
    hwnd = get_my_hwnd()
    if hwnd is None:
        return 'SC_ID: hwnd=UNKNOWN (not bootstrapped)'
    scid = get_my_scid() or 'unknown'
    return f'SC_ID: hwnd=0x{hwnd:08x} scid={scid}'


def verify_peer(hwnd: int) -> Tuple[bool, Optional['BirthTag']]:
    """Verify a peer's identity via the enterprise birth tag registry.

    Returns (True, BirthTag) if verified, (False, None) if not.
    Requires selfconnect-enterprise to be available.
    """
    if not _REGISTRY_AVAILABLE:
        return False, None
    tag = read_birth_tag(hwnd)
    if tag is None:
        return False, None
    ok = verify_tag(tag)
    return ok, tag if ok else None


def peer_from_message(text: str) -> Optional[int]:
    """Extract the sender's claimed HWND from an SC_ID line in a received message."""
    m = re.search(r'SC_ID:.*?hwnd=0x([0-9a-fA-F]+)', text)
    return int(m.group(1), 16) if m else None


# ── CLI: python sc_session.py status / set <hwnd> / verify <hwnd> ─────────────

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='SelfConnect session identity tool')
    sub = ap.add_subparsers(dest='cmd')

    sub.add_parser('status', help='Show current session identity')

    p_set = sub.add_parser('set', help='Manually set this session HWND')
    p_set.add_argument('hwnd', help='HWND as hex (0x...) or decimal')

    p_verify = sub.add_parser('verify', help='Verify a peer HWND')
    p_verify.add_argument('hwnd', help='Peer HWND as hex or decimal')

    args = ap.parse_args()

    if args.cmd == 'status':
        hwnd = get_my_hwnd()
        print(f'my_hwnd  = {f"0x{hwnd:08x}" if hwnd else "NOT SET"}')
        print(f'my_scid  = {get_my_scid() or "NOT SET"}')
        print(f'registry = {"available" if _REGISTRY_AVAILABLE else "NOT AVAILABLE"}')

    elif args.cmd == 'set':
        hwnd = int(args.hwnd, 16) if args.hwnd.startswith('0x') else int(args.hwnd)
        set_my_hwnd(hwnd)
        print(f'Stored my_hwnd = 0x{hwnd:08x}')

    elif args.cmd == 'verify':
        hwnd = int(args.hwnd, 16) if args.hwnd.startswith('0x') else int(args.hwnd)
        ok, tag = verify_peer(hwnd)
        if ok:
            print(f'VERIFIED: {tag}')
        else:
            tag_raw = read_birth_tag(hwnd) if _REGISTRY_AVAILABLE else None
            if tag_raw is None:
                print(f'UNVERIFIED: no birth tag found at 0x{hwnd:08x}')
            else:
                print(f'FAILED: tag exists but verification failed: {tag_raw}')
