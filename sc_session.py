"""sc_session.py — Agent-side session identity helpers for SelfConnect.

Usage pattern:
    from sc_session import get_my_hwnd, verify_peer, bootstrap_from_briefing

    # Called when the agent receives its first SC_BOOTSTRAP message:
    bootstrap_from_briefing("SC_BOOTSTRAP: your_hwnd=0x00490d60 your_scid=claude-code-spawn-...")

    # Called before trusting any message from a peer:
    ok, tag = verify_peer(claimed_hwnd)
    if not ok:
        raise RuntimeError(f"Peer at 0x{claimed_hwnd:08x} failed identity verification")

    # If session_stamp.py wrote needs_set=True (couldn't auto-discover HWND):
    status = session_status()
    if status["needs_set"]:
        # Agent sees this and runs: python sc_session.py set <hwnd>
        print(status["set_hint"])
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
    """Return this agent's known Windows Terminal HWND, or None if not bootstrapped.

    If session_stamp.py wrote needs_set=True, returns None and session_status()
    will include a set_hint explaining how to self-correct.
    """
    hwnd = _session.get('my_hwnd')
    # Treat hwnd=0 (stamp fallback sentinel) the same as unset
    return hwnd if hwnd else None


def session_status() -> dict:
    """Return a dict summarising this session's identity state.

    Keys:
      hwnd        — int or None
      hwnd_hex    — "0x00490d60" or "NOT SET"
      scid        — str or None
      verified    — bool
      needs_set   — True if stamp wrote hwnd=0 (auto-discovery failed)
      reason      — why the hwnd was chosen (from session_stamp.py)
      set_hint    — human/agent-readable instruction when needs_set is True
    """
    # Prefer the enterprise .sessions/current.json if it exists and is richer
    ent_file = Path(__file__).parent.parent / 'selfconnect-enterprise' / '.sessions' / 'current.json'
    src: dict = {}
    if ent_file.exists():
        try:
            src = json.loads(ent_file.read_text(encoding='utf-8'))
        except Exception:
            pass

    hwnd      = src.get('hwnd') or _session.get('my_hwnd') or 0
    scid      = src.get('scid') or _session.get('my_scid') or ''
    verified  = src.get('verified', False)
    needs_set = src.get('needs_set', hwnd == 0)
    reason    = src.get('reason', 'manual-set' if hwnd else 'not-bootstrapped')

    set_hint = ''
    if needs_set or not hwnd:
        set_hint = (
            "HWND auto-discovery failed. To self-correct:\n"
            "  1. From AXIOM: list_windows() → find your tab → send hwnd\n"
            "  2. Or run:  python sc_session.py set <hwnd_hex>\n"
            "  3. Or set env var SC_SESSION_HWND=<hwnd_hex> before next session"
        )

    return {
        'hwnd':      hwnd or None,
        'hwnd_hex':  f"0x{hwnd:08x}" if hwnd else 'NOT SET',
        'scid':      scid or None,
        'verified':  verified,
        'needs_set': needs_set,
        'reason':    reason,
        'set_hint':  set_hint,
    }


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
        s = session_status()
        print(f'hwnd     = {s["hwnd_hex"]}')
        print(f'scid     = {s["scid"] or "NOT SET"}')
        print(f'verified = {s["verified"]}')
        print(f'reason   = {s["reason"]}')
        print(f'registry = {"available" if _REGISTRY_AVAILABLE else "NOT AVAILABLE"}')
        if s['needs_set']:
            print()
            print(s['set_hint'])

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
