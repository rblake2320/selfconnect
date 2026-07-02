#!/usr/bin/env python3
"""sc_resume.py — Session resume CLI for SelfConnect agents.

Commands
--------
  python sc_resume.py list                     List all sessions
  python sc_resume.py list --state open        List open/interrupted sessions
  python sc_resume.py verify <session-id>      Verify chain without resuming
  python sc_resume.py verify-index             Verify the session index chain
  python sc_resume.py resume <session-id>      Verify and resume a session
  python sc_resume.py resume <id> --interactive  Resume in interactive REPL mode

SECURITY (Fix 10)
-----------------
Before resuming, verify_for_resume() is called. If the chain is broken,
the first-event hash does not match the manifest, or rollback/fork is
suspected, the resume is blocked and the reason is printed.

A session in INTERRUPTED state (unclean shutdown) is resumable — it is
not automatically treated as tampering. The user is warned and must
confirm with --force-interrupted.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

_ENTERPRISE_AVAILABLE = False
try:
    from enterprise.provenance import SessionState
    from enterprise.session_index import SessionIndex
    _ENTERPRISE_AVAILABLE = True
except ImportError:
    pass


def _get_index(log_dir: Optional[str] = None) -> SessionIndex:
    if not _ENTERPRISE_AVAILABLE:
        print("ERROR: selfconnect-enterprise not installed.", file=sys.stderr)
        sys.exit(1)
    index_dir = Path(log_dir) if log_dir else None
    return SessionIndex(index_dir=index_dir)


def cmd_list(args: argparse.Namespace) -> int:
    index = _get_index(getattr(args, "log_dir", None))
    sessions = index.list_sessions(
        state_filter=getattr(args, "state", None),
        limit=getattr(args, "limit", 50),
    )
    if not sessions:
        print("No sessions found.")
        return 0

    print(f"\n{'SESSION ID':<38} {'STATE':<14} {'ROLE/AGENT':<24} {'STARTED':<26} {'EVENTS'}")
    print("-" * 120)
    for s in sessions:
        print(
            f"{s.session_id:<38} "
            f"{s.session_state:<14} "
            f"{s.agent_id[:22]:<24} "
            f"{s.started_at[:25]:<26} "
            f"{s.last_known_seq}"
        )
    print()
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    index = _get_index(getattr(args, "log_dir", None))
    session_id = args.session_id
    result = index.verify_for_resume(session_id)
    print(f"\nSession : {result.session_id}")
    print(f"OK      : {result.ok}")
    print(f"Message : {result.message}")
    if result.chain_result:
        cr = result.chain_result
        print(f"Chain   : {cr.count} events, state={cr.session_state}, "
              f"high_water={cr.high_water_seq}")
    if result.rollback_suspected:
        print("WARNING : Rollback suspected — log may have been replaced with older version.")
    if result.fork_suspected:
        print("WARNING : Fork suspected — local and remote receipts disagree.")
    print()
    return 0 if result.ok else 1


def cmd_verify_index(args: argparse.Namespace) -> int:
    index = _get_index(getattr(args, "log_dir", None))
    ok, message = index.verify_index_chain()
    print(f"\nIndex chain: {'OK' if ok else 'FAILED'} — {message}\n")
    return 0 if ok else 1


def cmd_resume(args: argparse.Namespace) -> int:
    index = _get_index(getattr(args, "log_dir", None))
    session_id = args.session_id
    result = index.verify_for_resume(session_id)

    if not result.ok:
        print(f"\nERROR: Cannot resume session {session_id!r}:")
        print(f"  {result.message}")
        if result.rollback_suspected:
            print("  Rollback suspected. Do not resume — evidence may be compromised.")
        if result.fork_suspected:
            print("  Fork suspected. Do not resume — history integrity cannot be guaranteed.")
        print()
        return 1

    entry = result.manifest_entry
    if entry and entry.session_state == SessionState.INTERRUPTED.value:
        if not getattr(args, "force_interrupted", False):
            print(
                f"\nWARNING: Session {session_id!r} ended uncleanly "
                f"(state=interrupted)."
            )
            print("  The chain is intact — this is likely a crash, not tampering.")
            print("  Use --force-interrupted to resume anyway.\n")
            return 1
        print(
            f"\nWARNING: Resuming interrupted session {session_id!r} "
            f"(chain intact, state=interrupted)."
        )

    # Build sc_shell.py arguments for resume
    cmd = [
        sys.executable, "sc_shell.py",
        "--session-id", session_id,
        "--audit-mode", entry.audit_mode if entry else "consumer",
    ]
    if entry:
        cmd += ["--agent-id", entry.agent_id]
    if getattr(args, "interactive", False):
        cmd.append("--interactive")
    log_dir = getattr(args, "log_dir", None)
    if log_dir:
        cmd += ["--log-dir", log_dir]

    print(f"\nResuming session {session_id!r}...")
    print(f"  {' '.join(cmd)}\n")

    try:
        result_proc = subprocess.run(cmd, check=False)
        return result_proc.returncode
    except FileNotFoundError:
        print("ERROR: sc_shell.py not found in current directory.", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="SelfConnect session resume CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--log-dir", default=None,
                   help="Provenance log/index directory (default: ~/.selfconnect/provenance)")

    sub = p.add_subparsers(dest="command")

    # list
    ls = sub.add_parser("list", help="List sessions")
    ls.add_argument("--state", default=None,
                    choices=["open", "sealed", "interrupted", "reconstructed"],
                    help="Filter by session state")
    ls.add_argument("--limit", type=int, default=50,
                    help="Maximum sessions to show (default: 50)")

    # verify
    vf = sub.add_parser("verify", help="Verify a session chain")
    vf.add_argument("session_id", help="Session ID to verify")

    # verify-index
    sub.add_parser("verify-index", help="Verify the session index chain integrity")

    # resume
    rs = sub.add_parser("resume", help="Verify and resume a session")
    rs.add_argument("session_id", help="Session ID to resume")
    rs.add_argument("--interactive", action="store_true",
                    help="Start in interactive REPL mode")
    rs.add_argument("--force-interrupted", action="store_true",
                    help="Resume an interrupted session without confirmation")

    return p


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "list":
        sys.exit(cmd_list(args))
    elif args.command == "verify":
        sys.exit(cmd_verify(args))
    elif args.command == "verify-index":
        sys.exit(cmd_verify_index(args))
    elif args.command == "resume":
        sys.exit(cmd_resume(args))
    else:
        parser.print_help()
        sys.exit(0)
