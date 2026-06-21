"""Fabric V2 session router with restart-safe replay state."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sc_fabric_v2

SCHEMA_VERSION = 1


class FabricRouterError(Exception):
    """Base class for router errors."""


class UnknownRouteError(FabricRouterError):
    """Raised when a frame targets an unregistered receiver."""


@dataclass
class RouterAgent:
    role: str
    birth_id: str
    status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "birth_id": self.birth_id, "status": self.status}


class FabricSessionRouter:
    """Route Fabric frames to durable agent mailboxes with replay persistence."""

    def __init__(
        self,
        *,
        session: sc_fabric_v2.FabricSession,
        mailbox_depth: int = 100,
        router_id: str | None = None,
    ) -> None:
        self.session = session
        self.mailbox_depth = int(mailbox_depth)
        self.router_id = router_id or f"router-{uuid.uuid4().hex[:8]}"
        self._agents: dict[str, RouterAgent] = {}
        self._mailboxes: dict[str, sc_fabric_v2.BoundedMailbox] = {}
        self._events: list[dict[str, Any]] = []

    def register_agent(self, *, role: str, birth_id: str, status: str = "active") -> None:
        if not role or not birth_id:
            raise ValueError("role and birth_id are required")
        self._agents[birth_id] = RouterAgent(role=role, birth_id=birth_id, status=status)
        self._mailboxes.setdefault(
            birth_id,
            sc_fabric_v2.BoundedMailbox(birth_id, max_depth=self.mailbox_depth),
        )

    def route_frame(self, frame: bytes) -> bytes:
        verified = self.session.open(frame)
        if verified.receiver not in self._agents:
            raise UnknownRouteError(f"unknown receiver: {verified.receiver}")
        mailbox = self._mailboxes[verified.receiver]
        mailbox.put(frame, timeout_ms=1)
        event = {
            "sender": verified.sender,
            "receiver": verified.receiver,
            "sequence": verified.sequence,
            "payload_hash": verified.payload_hash,
            "frame_hash": verified.frame_hash,
            "created_at": time.time(),
        }
        self._events.append(event)
        return self.session.seal(
            sender=verified.receiver,
            receiver=verified.sender,
            payload=f"ACK:{verified.sender}:{verified.sequence}",
            message_type="ack",
        )

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "router_id": self.router_id,
            "session_id": self.session.session_id,
            "agents": [agent.to_dict() for agent in self._agents.values()],
            "mailboxes": {
                birth_id: {
                    "depth": mailbox.depth(),
                    "max_depth": mailbox.max_depth,
                }
                for birth_id, mailbox in self._mailboxes.items()
            },
            "accepted_sequences": self.session.export_replay_state(),
            "events": list(self._events),
            "raw_text_included": False,
        }

    def save_state(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.snapshot_state(), indent=2, sort_keys=True), encoding="utf-8")
        return target

    @classmethod
    def from_state(
        cls,
        state: dict[str, Any],
        *,
        session: sc_fabric_v2.FabricSession,
        mailbox_depth: int = 100,
    ) -> FabricSessionRouter:
        router = cls(
            session=session,
            mailbox_depth=mailbox_depth,
            router_id=str(state.get("router_id") or f"router-{uuid.uuid4().hex[:8]}"),
        )
        for agent in state.get("agents", []):
            router.register_agent(
                role=str(agent["role"]),
                birth_id=str(agent["birth_id"]),
                status=str(agent.get("status", "active")),
            )
        session.import_replay_state(list(state.get("accepted_sequences", [])))
        router._events = list(state.get("events", []))
        return router


def load_state(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def selftest(*, output_dir: str | Path = "experiments/fabric_v2/results") -> dict[str, Any]:
    secret = f"router-secret-{uuid.uuid4().hex}"
    session_id = f"sfv2-router-{uuid.uuid4().hex[:8]}"
    session = sc_fabric_v2.FabricSession.from_secret(secret, session_id=session_id)
    router = FabricSessionRouter(session=session, mailbox_depth=10)
    for role in ("router-a", "router-b", "router-c"):
        router.register_agent(role=role, birth_id=role)

    first_frame = session.seal(sender="router-a", receiver="router-b", payload="SC_ROUTER_FIRST")
    first_ack = session.open(router.route_frame(first_frame), expected_receiver="router-a")

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / f"fabric_v2_router_state_{time.strftime('%Y%m%d_%H%M%S')}_redacted.json"
    router.save_state(state_path)

    restored_session = sc_fabric_v2.FabricSession.from_secret(secret, session_id=session_id)
    restored_router = FabricSessionRouter.from_state(
        load_state(state_path),
        session=restored_session,
        mailbox_depth=10,
    )

    replay_rejected = False
    try:
        restored_router.route_frame(first_frame)
    except sc_fabric_v2.ReplayRejectedError:
        replay_rejected = True

    second_frame = restored_session.seal(
        sender="router-a",
        receiver="router-c",
        payload="SC_ROUTER_AFTER_RESTART",
        sequence=2,
    )
    second_ack = restored_session.open(
        restored_router.route_frame(second_frame),
        expected_receiver="router-a",
    )
    final_state = restored_router.snapshot_state()
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "suite": "fabric_v2_router_restart_selftest",
        "ok": (
            first_ack.payload == b"ACK:router-a:1"
            and replay_rejected
            and second_ack.payload == b"ACK:router-a:2"
            and len(final_state["accepted_sequences"]) >= 2
        ),
        "router_id_hash": hashlib.sha256(router.router_id.encode("utf-8")).hexdigest(),
        "session_id_hash": hashlib.sha256(session_id.encode("utf-8")).hexdigest(),
        "agent_count": len(final_state["agents"]),
        "mailbox_depths": {
            birth_id: item["depth"]
            for birth_id, item in final_state["mailboxes"].items()
        },
        "accepted_sequence_count": len(final_state["accepted_sequences"]),
        "recovered_replay_state": replay_rejected,
        "mailbox_payload_recovery": False,
        "first_ack_payload": first_ack.payload.decode("utf-8"),
        "second_ack_payload": second_ack.payload.decode("utf-8"),
        "replay_rejected_after_restart": replay_rejected,
        "state_path": str(state_path),
        "raw_text_included": False,
        "created_at": time.time(),
    }
    artifact_path = root / f"fabric_v2_router_restart_selftest_{time.strftime('%Y%m%d_%H%M%S')}_redacted.json"
    artifact["artifact_path"] = str(artifact_path)
    artifact_path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return artifact


def _print_json(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ok", False) else 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="selfconnect-fabric-router", description="SelfConnect Fabric V2 router")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("selftest")
    p.add_argument("--output-dir", default="experiments/fabric_v2/results")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "selftest":
        return _print_json(selftest(output_dir=args.output_dir))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
