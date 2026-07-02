#!/usr/bin/env python3
"""sc_shell.py — SelfConnect pre-instructed agent boot script.

This is the entry point for a SelfConnect agent session. It:
  1. Parses role, audit mode, mesh socket, and parent session arguments.
  2. Starts the ProvenanceRecorder BEFORE doing anything else (fail-closed).
  3. Registers the session in the SessionIndex.
  4. Loads the role-specific boot profile (instructions, routing rules).
  5. Registers into the mesh if a mesh socket is provided.
  6. Runs the agent's main loop.
  7. Seals the session on clean exit.

FAIL-CLOSED BEHAVIOUR
---------------------
In enterprise and military audit modes, if the ProvenanceRecorder cannot
start (disk full, permissions error, sink unavailable), the script exits
with code 2 before executing any agent logic. The agent cannot act if it
cannot record.

In consumer mode, provenance is best-effort. The agent continues even if
the recorder fails to start.

NAMED PIPE SECURITY (Fix 5 — Phase 2 hardening)
------------------------------------------------
In the full Windows service deployment, the ProvenanceRecorder runs as a
separate service SID (the only process with FILE_APPEND_DATA rights to the
log). Agents connect to it via a named pipe with:
  - An unpredictable per-session pipe name (derived from a shared secret).
  - Mutual authentication: the recorder proves it holds the service key
    before the agent sends any events.
  - The agent connects with SECURITY_IDENTIFICATION flag to prevent token
    theft by a rogue pipe server.
This file documents the Phase 1 (in-process) implementation. The Phase 2
Windows service is tracked in GitHub issue #XX.

POLICY DOWNGRADE PROTECTION (Fix 11)
-------------------------------------
The audit_mode is recorded as the first event in the session. Any attempt
to downgrade it mid-session requires the orchestrator_token and is logged
as a signed AUDIT_MODE_CHANGE event. Downgrade attempts without the token
are logged as POLICY_VIOLATION events and blocked.

OS CORROBORATION (Fix 12)
--------------------------
If the OS telemetry sensor (Sysmon, ETW, auditd) stops emitting, a
TELEMETRY_GAP event is recorded. The os_corroboration field in SessionEvent
is reserved for future ETW/Sysmon correlation.

Usage
-----
  python sc_shell.py [options]

  --role ROLE           Agent role (default: "default")
  --audit-mode MODE     consumer | enterprise | military (default: consumer)
  --session-id ID       Resume an existing session by ID
  --parent-id ID        Parent session ID (for spawned agents)
  --mesh-socket PATH    Unix socket / named pipe for mesh registration
  --log-dir DIR         Override provenance log directory
  --orchestrator-token  Token required to close the session (enterprise/military)
  --supervisor-id ID    Identity of the supervisor/orchestrator
  --interactive         Drop into interactive REPL after boot (default: False)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger("sc_shell")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ---------------------------------------------------------------------------
# Attempt to import SelfConnect enterprise modules.
# If not installed, fall back gracefully in consumer mode.
# ---------------------------------------------------------------------------

_ENTERPRISE_AVAILABLE = False
try:
    from enterprise.provenance import (
        AuditMode,
        InMemoryWitnessSink,
        ProvenanceRecorder,
        ProvenanceRecorderError,
        SessionEventType,
    )
    from enterprise.session_index import SessionIndex
    _ENTERPRISE_AVAILABLE = True
except ImportError:
    logger.warning(
        "selfconnect-enterprise not installed. "
        "Provenance recording unavailable. "
        "Install with: pip install -e /path/to/selfconnect-enterprise"
    )


# ---------------------------------------------------------------------------
# Boot profiles
# ---------------------------------------------------------------------------

_DEFAULT_BOOT_PROFILES: dict[str, dict] = {
    "default": {
        "description": "Default SelfConnect agent",
        "allowed_event_types": ["tool_call", "tool_result", "shell_exec",
                                 "file_write", "file_read", "network_request",
                                 "checkpoint", "mesh_message"],
        "routing_rules": {},
    },
    "observer": {
        "description": "Read-only observer agent",
        "allowed_event_types": ["file_read", "network_request", "checkpoint",
                                 "mesh_message"],
        "routing_rules": {"read_only": True},
    },
    "orchestrator": {
        "description": "Orchestrator / supervisor agent",
        "allowed_event_types": ["*"],  # all event types
        "routing_rules": {"can_spawn": True, "can_approve": True},
    },
    "worker": {
        "description": "Task worker agent",
        "allowed_event_types": ["tool_call", "tool_result", "shell_exec",
                                 "file_write", "file_read", "checkpoint"],
        "routing_rules": {},
    },
}


def load_boot_profile(role: str, profiles_dir: Optional[Path] = None) -> dict:
    """Load a boot profile for the given role.

    Checks profiles_dir first, then falls back to built-in defaults.
    """
    if profiles_dir is not None:
        profile_path = profiles_dir / f"{role}.json"
        if profile_path.exists():
            try:
                with open(profile_path, encoding="utf-8") as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load profile %s: %s", profile_path, exc)

    return _DEFAULT_BOOT_PROFILES.get(role, _DEFAULT_BOOT_PROFILES["default"])


# ---------------------------------------------------------------------------
# Mesh registration
# ---------------------------------------------------------------------------

def register_with_mesh(
    session_id: str,
    agent_id: str,
    role: str,
    mesh_socket: Optional[str],
    recorder: Optional[ProvenanceRecorder],
) -> bool:
    """Register this agent with the mesh coordinator.

    Returns True on success, False if mesh is unavailable (non-fatal).
    """
    if mesh_socket is None:
        return False

    try:
        import socket as _socket

        payload = json.dumps({
            "action": "register",
            "session_id": session_id,
            "agent_id": agent_id,
            "role": role,
        }).encode("utf-8")

        if mesh_socket.startswith(r"\\.\pipe\\") or (
            sys.platform == "win32" and not mesh_socket.startswith("/")
        ):
            # Windows named pipe
            logger.info("Mesh registration via named pipe: %s", mesh_socket)
            # Stub: full named pipe client with SECURITY_IDENTIFICATION is
            # implemented in the Phase 2 Windows service deployment.
            # See Fix 5 documentation in module docstring.
            logger.warning(
                "Named pipe mesh registration stub — implement Phase 2 "
                "Windows service for production use."
            )
            return False
        else:
            # Unix domain socket
            with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as sock:
                sock.settimeout(3.0)
                sock.connect(mesh_socket)
                sock.sendall(payload + b"\n")
                response = sock.recv(1024)
                result = json.loads(response.decode("utf-8"))
                if result.get("status") == "ok":
                    if recorder is not None:
                        recorder.record(
                            SessionEventType.MESH_MESSAGE,
                            payload={"action": "registered", "mesh_socket": mesh_socket},
                        )
                    return True
    except Exception as exc:
        logger.warning("Mesh registration failed (non-fatal): %s", exc)

    return False


# ---------------------------------------------------------------------------
# Main boot sequence
# ---------------------------------------------------------------------------

def boot(args: argparse.Namespace) -> int:
    """Execute the full agent boot sequence.

    Returns exit code: 0 = success, 1 = error, 2 = fail-closed (no provenance).
    """
    session_id = args.session_id or str(uuid.uuid4())
    agent_id = args.agent_id or f"sc-agent-{session_id[:8]}"
    audit_mode_str = args.audit_mode or "consumer"
    role = args.role or "default"
    log_dir = Path(args.log_dir) if args.log_dir else None

    logger.info(
        "Booting SelfConnect agent: session=%s role=%s audit_mode=%s",
        session_id, role, audit_mode_str,
    )

    # ── Step 1: Start ProvenanceRecorder (fail-closed) ────────────────────
    recorder: Optional[ProvenanceRecorder] = None
    index: Optional[SessionIndex] = None

    if _ENTERPRISE_AVAILABLE:
        try:
            audit_mode = AuditMode(audit_mode_str)

            # For testing: use InMemoryWitnessSink if military mode and no
            # external sink configured. In production, configure S3ObjectLockSink.
            replication_sink = None
            if audit_mode == AuditMode.MILITARY:
                logger.warning(
                    "Military mode: using InMemoryWitnessSink for testing. "
                    "Configure S3ObjectLockSink for production."
                )
                replication_sink = InMemoryWitnessSink()

            recorder = ProvenanceRecorder(
                session_id=session_id,
                agent_id=agent_id,
                audit_mode=audit_mode,
                log_dir=log_dir,
                supervisor_id=args.supervisor_id,
                orchestrator_token=args.orchestrator_token,
                replication_sink=replication_sink,
            )
            recorder.start()
            logger.info(
                "Provenance recorder started: %s", recorder.log_path
            )

            # Register session in index
            index = SessionIndex(
                index_dir=log_dir or (Path.home() / ".selfconnect" / "provenance"),
            )
            index.open_session(recorder)

        except ProvenanceRecorderError as exc:
            logger.critical("Provenance recorder failed to start: %s", exc)
            if audit_mode_str in ("enterprise", "military"):
                logger.critical(
                    "FAIL-CLOSED: agent cannot start in %s mode without provenance.",
                    audit_mode_str,
                )
                return 2
            logger.warning("Consumer mode: continuing without provenance.")
            recorder = None
    else:
        if audit_mode_str in ("enterprise", "military"):
            logger.critical(
                "FAIL-CLOSED: selfconnect-enterprise not installed. "
                "Cannot start in %s mode without provenance.", audit_mode_str
            )
            return 2
        logger.warning("Consumer mode: provenance unavailable (enterprise not installed).")

    # ── Step 2: Load boot profile ─────────────────────────────────────────
    profile = load_boot_profile(role)
    logger.info("Boot profile loaded: %s — %s", role, profile.get("description", ""))

    if recorder is not None:
        recorder.record(
            SessionEventType.CHECKPOINT,
            payload={
                "checkpoint": "boot_profile_loaded",
                "role": role,
                "profile_description": profile.get("description", ""),
            },
        )

    # ── Step 3: Mesh registration ─────────────────────────────────────────
    mesh_registered = register_with_mesh(
        session_id=session_id,
        agent_id=agent_id,
        role=role,
        mesh_socket=args.mesh_socket,
        recorder=recorder,
    )
    if args.mesh_socket and not mesh_registered:
        logger.warning("Mesh registration failed — continuing in standalone mode.")

    # ── Step 4: Parent session linkage ────────────────────────────────────
    if args.parent_id and recorder is not None:
        recorder.record(
            SessionEventType.AGENT_SPAWN,
            payload={
                "parent_session_id": args.parent_id,
                "child_session_id": session_id,
                "role": role,
            },
        )

    # ── Step 5: Main agent loop ───────────────────────────────────────────
    exit_code = 0
    try:
        if args.interactive:
            exit_code = _interactive_loop(session_id, agent_id, role, recorder, profile)
        else:
            logger.info(
                "Agent boot complete. session=%s ready. "
                "(Use --interactive for REPL mode.)", session_id
            )
            if recorder is not None:
                recorder.record(
                    SessionEventType.CHECKPOINT,
                    payload={"checkpoint": "boot_complete", "session_id": session_id},
                )

    except KeyboardInterrupt:
        logger.info("Interrupted.")
        if recorder is not None:
            try:
                recorder.record(
                    SessionEventType.SESSION_INTERRUPT,
                    payload={"reason": "keyboard_interrupt"},
                )
            except Exception:
                pass
        exit_code = 0

    except Exception as exc:
        logger.exception("Unhandled exception in agent loop: %s", exc)
        if recorder is not None:
            try:
                recorder.record(
                    SessionEventType.POLICY_VIOLATION,
                    payload={"error": str(exc), "type": type(exc).__name__},
                )
            except Exception:
                pass
        exit_code = 1

    finally:
        # ── Step 6: Seal session ──────────────────────────────────────────
        if recorder is not None and not recorder.is_closed:
            try:
                recorder.close(
                    summary={"exit_code": exit_code, "role": role},
                    orchestrator_token=args.orchestrator_token,
                )
                if index is not None:
                    from enterprise.provenance import SessionState
                    index.update_session(
                        session_id,
                        recorder=recorder,
                        state=SessionState.SEALED,
                        summary={"exit_code": exit_code},
                    )
                logger.info(
                    "Session sealed: %s (%d events)", session_id, recorder.event_count
                )
            except Exception as exc:
                logger.warning("Failed to seal session: %s", exc)

    return exit_code


# ---------------------------------------------------------------------------
# Interactive REPL (minimal — for development/testing)
# ---------------------------------------------------------------------------

def _interactive_loop(
    session_id: str,
    agent_id: str,
    role: str,
    recorder: Optional[ProvenanceRecorder],
    profile: dict,
) -> int:
    """Minimal interactive REPL for development and testing."""
    print("\nSelfConnect Agent REPL")
    print(f"  session : {session_id}")
    print(f"  agent   : {agent_id}")
    print(f"  role    : {role}")
    print(f"  profile : {profile.get('description', 'unknown')}")
    print("  type 'exit' or Ctrl-C to quit\n")

    while True:
        try:
            cmd = input(f"[{role}]> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not cmd:
            continue
        if cmd.lower() in ("exit", "quit", "q"):
            break
        if cmd.lower() == "status":
            if recorder is not None:
                print(f"  events : {recorder.event_count}")
                print(f"  state  : {recorder.session_state.value}")
                print(f"  log    : {recorder.log_path}")
            else:
                print("  provenance: unavailable")
            continue
        if cmd.lower() == "tail":
            if recorder is not None:
                for r in recorder.tail(5):
                    print(f"  [{r.get('seq')}] {r.get('event_type')} {r.get('ts', '')[:19]}")
            continue
        if cmd.lower() == "verify":
            if recorder is not None:
                result = recorder.verify()
                print(f"  ok={result.ok} count={result.count} state={result.session_state}")
                print(f"  {result.message}")
            continue

        # Record arbitrary tool_call events in REPL mode
        if recorder is not None:
            recorder.record(
                SessionEventType.TOOL_CALL,
                payload={"command": cmd},
            )
        print(f"  recorded: tool_call — {cmd!r}")

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="SelfConnect agent boot script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--role", default="default",
                   help="Agent role (default: default)")
    p.add_argument("--audit-mode", default="consumer",
                   choices=["consumer", "enterprise", "military"],
                   help="Audit mode (default: consumer)")
    p.add_argument("--session-id", default=None,
                   help="Session ID (auto-generated if not provided)")
    p.add_argument("--agent-id", default=None,
                   help="Agent identity string")
    p.add_argument("--parent-id", default=None,
                   help="Parent session ID for spawned agents")
    p.add_argument("--mesh-socket", default=None,
                   help="Unix socket or named pipe for mesh registration")
    p.add_argument("--log-dir", default=None,
                   help="Override provenance log directory")
    p.add_argument("--orchestrator-token", default=None,
                   help="Token required to close the session (enterprise/military)")
    p.add_argument("--supervisor-id", default=None,
                   help="Identity of the supervisor/orchestrator")
    p.add_argument("--interactive", action="store_true",
                   help="Drop into interactive REPL after boot")
    return p


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(boot(args))
