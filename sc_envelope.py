"""sc_envelope — HMAC-signed message envelopes and agent cards for the mesh.

Fixes message authenticity: the ledger records what happened, but nothing
proved WHO sent an injected message — any local process could forge one.
Every inter-node message becomes an Envelope signed with a shared mesh key
(HMAC-SHA256 over JCS-style canonical JSON, constant-time verify). Agent
cards (A2A pattern) advertise each node's identity + capabilities, signed the
same way, so the hub can verify authenticity before dispatch.

Stdlib only. For asymmetric identity (Ed25519 / DID), layer sc_identity's
AgentIdentity on top — the ``sig_alg`` field leaves room for it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

__version__ = "0.12.0"

DEFAULT_KEY_PATH = Path.home() / ".selfconnect" / "mesh.key"
SIG_ALG = "hmac-sha256"
ENVELOPE_MAX_AGE_S = 300.0  # replayed signed messages older than this are rejected


class EnvelopeError(RuntimeError):
    pass


def load_or_create_mesh_key(path: Path | str = DEFAULT_KEY_PATH) -> bytes:
    """Load the shared mesh key, creating a random 32-byte one on first use."""
    path = Path(path)
    if path.exists():
        return bytes.fromhex(path.read_text(encoding="utf-8").strip())
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    path.write_text(key.hex(), encoding="utf-8")
    try:
        os.chmod(path, 0o600)  # best effort; NTFS ACLs differ
    except OSError:
        pass
    return key


def _canonical(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _signature(key: bytes, body: dict) -> str:
    return hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()


@dataclass
class Envelope:
    """One signed inter-node message. ``correlation_id`` carries the task id
    so every message threads back to a durable unit of work."""

    sender: str
    recipient: str
    kind: str  # e.g. "task.dispatch", "task.result", "doorbell", "ping"
    payload: dict = field(default_factory=dict)
    correlation_id: str = ""
    env_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    ts: float = field(default_factory=time.time)
    sig_alg: str = SIG_ALG
    sig: str = ""

    def _body(self) -> dict:
        d = asdict(self)
        d.pop("sig")
        return d

    def sign(self, key: bytes) -> Envelope:
        self.sig = _signature(key, self._body())
        return self

    def verify(self, key: bytes, max_age_s: float = 0.0) -> bool:
        """Constant-time signature check; optional replay window."""
        if not self.sig:
            return False
        expected = _signature(key, self._body())
        if not hmac.compare_digest(expected, self.sig):
            return False
        if max_age_s > 0 and (time.time() - self.ts) > max_age_s:
            return False
        return True

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str | bytes) -> Envelope:
        try:
            return cls(**json.loads(raw))
        except (json.JSONDecodeError, TypeError) as exc:
            raise EnvelopeError(f"malformed envelope: {exc}") from exc


@dataclass
class AgentCard:
    """A2A-style capability advertisement for one mesh node, signed so the
    hub verifies authenticity before dispatching to it."""

    name: str
    node: str  # e.g. "windows-a", "spark-1"
    version: str = __version__
    capabilities: list[str] = field(default_factory=list)
    endpoints: dict = field(default_factory=dict)  # e.g. {"hub": "...", "agent_port": 9877}
    issued_at: float = field(default_factory=time.time)
    sig_alg: str = SIG_ALG
    sig: str = ""

    def _body(self) -> dict:
        d = asdict(self)
        d.pop("sig")
        return d

    def sign(self, key: bytes) -> AgentCard:
        self.sig = _signature(key, self._body())
        return self

    def verify(self, key: bytes) -> bool:
        if not self.sig:
            return False
        return hmac.compare_digest(_signature(key, self._body()), self.sig)

    def to_dict(self) -> dict:
        return asdict(self)


def publish_card(card: AgentCard, directory: Path | str) -> Path:
    """Write ``<name>.card.json`` into a shared/hub directory."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{card.name}.card.json"
    path.write_text(json.dumps(card.to_dict(), indent=2), encoding="utf-8")
    return path


def load_cards(directory: Path | str, key: Optional[bytes] = None,
               require_valid: bool = True) -> list[AgentCard]:
    """Load all cards from a directory; with a key, drop invalid signatures
    (fail closed when ``require_valid``)."""
    directory = Path(directory)
    cards: list[AgentCard] = []
    if not directory.is_dir():
        return cards
    for p in sorted(directory.glob("*.card.json")):
        try:
            card = AgentCard(**json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError):
            continue
        if key is not None and require_valid and not card.verify(key):
            continue
        cards.append(card)
    return cards


__all__ = [
    "DEFAULT_KEY_PATH",
    "ENVELOPE_MAX_AGE_S",
    "SIG_ALG",
    "AgentCard",
    "Envelope",
    "EnvelopeError",
    "load_cards",
    "load_or_create_mesh_key",
    "publish_card",
]
