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
import math
import os
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

__version__ = "0.12.0"

DEFAULT_KEY_PATH = Path.home() / ".selfconnect" / "mesh.key"
DEFAULT_CREDENTIAL_TARGET = "SelfConnect/mesh/envelope-default"
SIG_ALG = "hmac-sha256"
ENVELOPE_MAX_AGE_S = 300.0  # replayed signed messages older than this are rejected
AGENT_CARD_MAX_AGE_S = 300.0
MAX_CLOCK_SKEW_S = 5.0
DEFAULT_REPLAY_PATH = Path(
    os.environ.get("LOCALAPPDATA", str(Path.home()))
) / "SelfConnect" / "envelope_replay.sqlite3"


class EnvelopeError(RuntimeError):
    pass


class EnvelopeReplayStore:
    """Durably consume each signed envelope ID exactly once."""

    def __init__(self, path: Path | str = DEFAULT_REPLAY_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS consumed_envelopes ("
                "env_id TEXT PRIMARY KEY, sender TEXT NOT NULL, "
                "signature TEXT NOT NULL, consumed_at REAL NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def consume(self, *, env_id: str, sender: str, signature: str, now: float) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO consumed_envelopes(env_id,sender,signature,consumed_at) "
                    "VALUES(?,?,?,?)",
                    (env_id, sender, signature, now),
                )
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def load_or_create_mesh_key(
    path: Path | str | None = None,
    *,
    allow_plaintext_file: bool = False,
) -> bytes:
    """Load/create the mesh key in Credential Manager by default.

    An explicit file path is a test/interop escape hatch and requires an
    equally explicit plaintext opt-in.
    """
    if path is None and os.name == "nt":
        from sc_windows_credentials import read_secret, write_secret

        stored = read_secret(DEFAULT_CREDENTIAL_TARGET)
        if stored is not None:
            if len(stored) != 32:
                raise EnvelopeError("stored mesh credential has an invalid length")
            return stored
        legacy = DEFAULT_KEY_PATH
        key = bytes.fromhex(legacy.read_text(encoding="utf-8").strip()) if legacy.exists() else os.urandom(32)
        if len(key) != 32:
            raise EnvelopeError("legacy mesh key must contain exactly 32 bytes")
        write_secret(DEFAULT_CREDENTIAL_TARGET, key)
        if not hmac.compare_digest(read_secret(DEFAULT_CREDENTIAL_TARGET) or b"", key):
            raise EnvelopeError("Credential Manager read-back verification failed")
        if legacy.exists():
            legacy.unlink()
        return key
    path = Path(path or DEFAULT_KEY_PATH)
    if not allow_plaintext_file:
        raise EnvelopeError("plaintext mesh-key files require allow_plaintext_file=True")
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
    env_id: str = field(default_factory=lambda: uuid.uuid4().hex)
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

    def verify(
        self,
        key: bytes,
        max_age_s: float = ENVELOPE_MAX_AGE_S,
        *,
        replay_store: EnvelopeReplayStore | None = None,
        now: float | None = None,
    ) -> bool:
        """Verify freshness and atomically consume this envelope exactly once."""
        if not self.sig:
            return False
        expected = _signature(key, self._body())
        if not hmac.compare_digest(expected, self.sig):
            return False
        if (
            type(self.env_id) is not str
            or len(self.env_id) != 32
            or any(ch not in "0123456789abcdef" for ch in self.env_id)
            or isinstance(self.ts, bool)
            or not isinstance(self.ts, (int, float))
            or not math.isfinite(float(self.ts))
            or isinstance(max_age_s, bool)
            or not isinstance(max_age_s, (int, float))
            or not math.isfinite(float(max_age_s))
            or float(max_age_s) <= 0
        ):
            return False
        checked_at = time.time() if now is None else now
        if (
            isinstance(checked_at, bool)
            or not isinstance(checked_at, (int, float))
            or not math.isfinite(float(checked_at))
            or float(self.ts) > float(checked_at) + MAX_CLOCK_SKEW_S
            or float(checked_at) - float(self.ts) > float(max_age_s)
        ):
            return False
        store = replay_store or EnvelopeReplayStore()
        return store.consume(
            env_id=self.env_id,
            sender=self.sender,
            signature=self.sig,
            now=float(checked_at),
        )

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

    def verify(
        self,
        key: bytes,
        max_age_s: float = AGENT_CARD_MAX_AGE_S,
        *,
        now: float | None = None,
    ) -> bool:
        if not self.sig:
            return False
        if not hmac.compare_digest(_signature(key, self._body()), self.sig):
            return False
        checked_at = time.time() if now is None else now
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in (self.issued_at, checked_at, max_age_s)
        ) or float(max_age_s) <= 0:
            return False
        return (
            float(self.issued_at) <= float(checked_at) + MAX_CLOCK_SKEW_S
            and float(checked_at) - float(self.issued_at) <= float(max_age_s)
        )

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
    "AGENT_CARD_MAX_AGE_S",
    "DEFAULT_KEY_PATH",
    "DEFAULT_REPLAY_PATH",
    "ENVELOPE_MAX_AGE_S",
    "SIG_ALG",
    "AgentCard",
    "Envelope",
    "EnvelopeError",
    "EnvelopeReplayStore",
    "load_cards",
    "load_or_create_mesh_key",
    "publish_card",
]
