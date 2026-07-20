"""Fabric V2 long-lived per-user service wrapper.

Wraps FabricHostService + FabricSessionRouter into a persistent,
restart-safe process-level service with periodic state saves and a
watchdog loop that logs stale agents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sc_fabric_v2
import sc_fabric_windows_svc as _winsvc
from sc_fabric_host import FabricHostService, host_roundtrip
from sc_fabric_router import FabricSessionRouter

SCHEMA_VERSION = 1


@dataclass
class FabricServiceConfig:
    """Configuration for FabricService."""

    state_dir: str = ""
    pipe_name: str = "SelfConnectFabricService"
    session_secret: str = ""
    session_id: str = ""
    mailbox_depth: int = 200
    request_timeout_s: float = 5.0
    watchdog_timeout_s: float = 60.0
    save_interval_s: float = 30.0


class FabricService:
    """Long-lived per-user Fabric V2 service.

    Wraps a FabricHostService (named-pipe ingress + IOCP dispatch) and a
    FabricSessionRouter (durable agent registry + replay state) into a
    persistent, restart-safe process.
    """

    def __init__(self, config: FabricServiceConfig | None = None) -> None:
        self.config = config or FabricServiceConfig()
        self._state_dir: Path = (
            Path(self.config.state_dir) if self.config.state_dir else Path.home() / ".selfconnect"
        )
        self._state_path: Path = self._state_dir / "fabric_service_state.json"
        self._pid_path: Path = self._state_dir / "fabric_service.pid"
        self._stop: threading.Event = threading.Event()
        self._session: sc_fabric_v2.FabricSession | None = None
        self._host: FabricHostService | None = None
        self._router: FabricSessionRouter | None = None
        self._saver_thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Identity helpers
    # ------------------------------------------------------------------

    def _effective_secret(self) -> str:
        if self.config.session_secret:
            return self.config.session_secret
        raw = socket.gethostname() + ":" + self.config.pipe_name
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _effective_session_id(self) -> str:
        if self.config.session_id:
            return self.config.session_id
        suffix = hashlib.sha256(self.config.pipe_name.encode()).hexdigest()[:8]
        return f"sfv2-service-{suffix}"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._pid_path.write_text(str(os.getpid()))

        secret = self._effective_secret()
        session_id = self._effective_session_id()
        session = sc_fabric_v2.FabricSession.from_secret(secret, session_id=session_id)
        self._session = session

        if self._state_path.exists():
            try:
                state = json.loads(self._state_path.read_text(encoding="utf-8"))
                self._router = FabricSessionRouter.from_state(
                    state,
                    session=session,
                    mailbox_depth=self.config.mailbox_depth,
                )
                session.import_replay_state(state.get("accepted_sequences", []))
            except Exception as exc:
                print(f"[FabricService] WARNING: failed to load state: {exc}", file=sys.stderr)
                self._router = FabricSessionRouter(
                    session=session,
                    mailbox_depth=self.config.mailbox_depth,
                )
        else:
            self._router = FabricSessionRouter(
                session=session,
                mailbox_depth=self.config.mailbox_depth,
            )

        self._host = FabricHostService(
            session=session,
            address=self.config.pipe_name,
            mailbox_depth=self.config.mailbox_depth,
            request_timeout_s=self.config.request_timeout_s,
        )
        self._host.start()

        self._stop.clear()

        self._saver_thread = threading.Thread(
            target=self._save_loop,
            name="sc-fabric-svc-saver",
            daemon=True,
        )
        self._saver_thread.start()

        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="sc-fabric-svc-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.save_state()
        if self._host is not None:
            self._host.stop()
        if self._saver_thread is not None:
            self._saver_thread.join(timeout=5.0)
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=5.0)
        try:
            if self._pid_path.exists():
                self._pid_path.unlink()
        except Exception:
            pass

    def save_state(self) -> Path:
        if self._router is None:
            return self._state_path
        state = self._router.snapshot_state()
        if self._session is not None:
            state["accepted_sequences"] = self._session.export_replay_state()
        if self._host is not None:
            state["host_stats"] = self._host.stats()
        state["service_pid"] = os.getpid()
        state["schema_version"] = SCHEMA_VERSION
        try:
            self._state_path.write_text(
                json.dumps(state, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"[FabricService] WARNING: save_state failed: {exc}", file=sys.stderr)
        return self._state_path

    # ------------------------------------------------------------------
    # Background threads
    # ------------------------------------------------------------------

    def _save_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(timeout=self.config.save_interval_s)
            if not self._stop.is_set():
                self.save_state()

    def _watchdog_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(timeout=10.0)
            if self._router is not None:
                stale = self._router.check_watchdog(
                    timeout_ns=int(self.config.watchdog_timeout_s * 1_000_000_000)
                )
                for birth_id in stale:
                    print(
                        f"[FabricService] WARNING: watchdog stale agent birth_id={birth_id!r}",
                        file=sys.stderr,
                    )

    # ------------------------------------------------------------------
    # Status / agent API
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        router_agents: list[dict[str, Any]] = []
        if self._router is not None:
            try:
                router_agents = self._router.snapshot_state().get("agents", [])
            except Exception:
                pass
        host_stats: dict[str, Any] = {}
        if self._host is not None:
            try:
                host_stats = self._host.stats()
            except Exception:
                pass
        return {
            "schema_version": SCHEMA_VERSION,
            "running": self._host is not None and not self._stop.is_set(),
            "pid": os.getpid(),
            "state_path": str(self._state_path),
            "pid_path": str(self._pid_path),
            "host_stats": host_stats,
            "router_agents": router_agents,
            "state_file_exists": self._state_path.exists(),
        }

    def register_agent(
        self,
        *,
        role: str,
        birth_id: str,
        hwnd: int = 0,
        pid: int = 0,
        task: str = "",
        status: str = "active",
    ) -> None:
        if self._router is None:
            raise RuntimeError("FabricService not started")
        self._router.register_agent(
            role=role,
            birth_id=birth_id,
            hwnd=hwnd,
            pid=pid,
            task=task,
            status=status,
        )

    def record_heartbeat(self, birth_id: str, now_ns: int | None = None) -> None:
        if self._router is None:
            raise RuntimeError("FabricService not started")
        self._router.record_heartbeat(birth_id, now_ns)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def selftest(*, output_dir: str | Path = "experiments/fabric_v2/results") -> dict[str, Any]:
    """End-to-end service selftest.

    Only runs on Windows. Creates an ephemeral service, does a host roundtrip,
    registers + heartbeats an agent, exercises the watchdog, saves/loads state,
    and checks restart coherence.
    """
    if sys.platform != "win32":
        return {
            "ok": False,
            "status": "na",
            "reason": "not_windows",
            "schema_version": SCHEMA_VERSION,
            "raw_text_included": False,
        }

    unique = uuid.uuid4().hex[:12]
    pipe_name = f"SelfConnectFabricSvcTest_{unique}"

    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "svc_state"

        config = FabricServiceConfig(
            state_dir=str(state_dir),
            pipe_name=pipe_name,
            session_secret=f"test-secret-{unique}",
            session_id=f"sfv2-svctest-{unique}",
            mailbox_depth=10,
            request_timeout_s=5.0,
            watchdog_timeout_s=60.0,
            save_interval_s=300.0,  # don't auto-save during test
        )

        service = FabricService(config)
        service.start()

        roundtrip_ok = False
        watchdog_ok = False
        restart_ok = False

        try:
            # --- host roundtrip through the service's named pipe ---
            assert service._session is not None
            assert service._host is not None
            rt = host_roundtrip(
                session=service._session,
                address=service._host.address,
                sender="svc-client",
                receiver="svc-server",
                payload="SC_FABRIC_SVC_SELFTEST",
            )
            roundtrip_ok = bool(rt.get("ok"))

            # --- agent registration + watchdog ---
            service.register_agent(role="svc-a", birth_id="svc-a")
            # Record a heartbeat at a very old timestamp so watchdog sees it as stale
            service._router.record_heartbeat("svc-a", now_ns=1000)
            stale = service._router.check_watchdog(now_ns=10**18, timeout_ns=1)
            watchdog_ok = "svc-a" in stale

            # --- save state ---
            saved_path = service.save_state()

        finally:
            service.stop()

        state_path = saved_path  # keep reference after context

        # Verify state file was written
        state_file_written = state_path.exists()

        # --- restart coherence: reload state into a new service ---
        config2 = FabricServiceConfig(
            state_dir=str(state_dir),
            pipe_name=f"SelfConnectFabricSvcTest2_{unique}",
            session_secret=f"test-secret-{unique}",
            session_id=f"sfv2-svctest-{unique}",
            mailbox_depth=10,
            request_timeout_s=5.0,
            watchdog_timeout_s=60.0,
            save_interval_s=300.0,
        )
        service2 = FabricService(config2)
        service2.start()
        try:
            pass
        finally:
            service2.stop()

        # Check that the state file still exists and contains svc-a
        loaded_state: dict[str, Any] = {}
        if state_path.exists():
            loaded_state = json.loads(state_path.read_text(encoding="utf-8"))

        agents_in_state = [a["birth_id"] for a in loaded_state.get("agents", [])]
        restart_ok = "svc-a" in agents_in_state

        artifact: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "suite": "fabric_v2_service_selftest",
            "ok": roundtrip_ok and watchdog_ok and state_file_written and restart_ok,
            "transport": "windows_named_pipe_af_pipe",
            "roundtrip_ok": roundtrip_ok,
            "watchdog_ok": watchdog_ok,
            "state_file_written": state_file_written,
            "restart_ok": restart_ok,
            "raw_text_included": False,
            "created_at": time.time(),
        }

        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        artifact_path = root / f"fabric_v2_service_selftest_{ts}_redacted.json"
        artifact["artifact_path"] = str(artifact_path)
        artifact_path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
        return artifact


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="selfconnect-fabric-service",
        description="SelfConnect Fabric V2 long-lived service",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Start the service and block until SIGTERM/Ctrl-C")
    p_start.add_argument("--state-dir", default="")
    p_start.add_argument("--pipe-name", default="SelfConnectFabricService")
    p_start.add_argument("--session-secret", default="")
    p_start.add_argument("--session-id", default="")
    p_start.add_argument("--mailbox-depth", type=int, default=200)
    p_start.add_argument("--request-timeout", type=float, default=5.0)
    p_start.add_argument("--watchdog-timeout", type=float, default=60.0)
    p_start.add_argument("--save-interval", type=float, default=30.0)

    p_stop = sub.add_parser("stop", help="Send SIGTERM to a running service (reads PID file)")
    p_stop.add_argument("--state-dir", default="")
    p_stop.add_argument("--pipe-name", default="SelfConnectFabricService")

    p_status = sub.add_parser("status", help="Print service status as JSON")
    p_status.add_argument("--state-dir", default="")
    p_status.add_argument("--pipe-name", default="SelfConnectFabricService")

    p_selftest = sub.add_parser("selftest", help="Run the built-in selftest")
    p_selftest.add_argument("--output-dir", default="experiments/fabric_v2/results")

    sub.add_parser("install-service", help="Install as a Windows SCM service (requires admin)")
    sub.add_parser("remove-service", help="Stop and remove the Windows SCM service (requires admin)")
    sub.add_parser("query-service", help="Query current Windows SCM service state")
    sub.add_parser("start-service", help="Start the Windows SCM service")
    sub.add_parser("stop-service", help="Stop the Windows SCM service")

    return parser


def _config_from_args(args: Any) -> FabricServiceConfig:
    return FabricServiceConfig(
        state_dir=args.state_dir,
        pipe_name=args.pipe_name,
    )


def _pid_path_from_args(args: Any) -> Path:
    state_dir = (
        Path(args.state_dir) if args.state_dir else Path.home() / ".selfconnect"
    )
    return state_dir / "fabric_service.pid"


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "start":
        config = FabricServiceConfig(
            state_dir=args.state_dir,
            pipe_name=args.pipe_name,
            session_secret=args.session_secret,
            session_id=args.session_id,
            mailbox_depth=args.mailbox_depth,
            request_timeout_s=args.request_timeout,
            watchdog_timeout_s=args.watchdog_timeout,
            save_interval_s=args.save_interval,
        )
        service = FabricService(config)
        service.start()

        stop_event = threading.Event()

        def _on_signal(signum: int, frame: Any) -> None:
            stop_event.set()

        try:
            signal.signal(signal.SIGTERM, _on_signal)
        except (OSError, ValueError):
            pass

        print(json.dumps({"event": "started", "pid": os.getpid()}), flush=True)
        try:
            stop_event.wait()
        except KeyboardInterrupt:
            pass
        finally:
            service.stop()
        return 0

    if args.command == "stop":
        pid_path = _pid_path_from_args(args)
        if not pid_path.exists():
            print(json.dumps({"ok": False, "error": "pid_file_not_found", "path": str(pid_path)}))
            return 2
        pid = int(pid_path.read_text().strip())
        try:
            os.kill(pid, signal.SIGTERM)
            print(json.dumps({"ok": True, "pid": pid}))
            return 0
        except ProcessLookupError:
            print(json.dumps({"ok": False, "error": "process_not_found", "pid": pid}))
            return 2

    if args.command == "status":
        config = _config_from_args(args)
        service = FabricService(config)
        print(json.dumps(service.status(), indent=2, sort_keys=True))
        return 0

    if args.command == "selftest":
        artifact = selftest(output_dir=args.output_dir)
        print(json.dumps(artifact, indent=2, sort_keys=True))
        return 0 if artifact.get("ok") else 2

    if args.command == "install-service":
        result = _winsvc.install_service()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("ok") else 2

    if args.command == "remove-service":
        result = _winsvc.remove_service()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("ok") else 2

    if args.command == "query-service":
        result = _winsvc.query_service()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("ok") else 2

    if args.command == "start-service":
        result = _winsvc.start_service()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("ok") else 2

    if args.command == "stop-service":
        result = _winsvc.stop_service()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("ok") else 2

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
