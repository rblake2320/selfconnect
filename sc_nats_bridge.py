"""sc_nats_bridge — signed envelopes over NATS/JetStream between mesh nodes.

The EdgeCitadel pattern: keep PostMessage injection as the LAST hop into a
terminal, but route inter-node envelopes through the NATS container already
running on :4222. That buys acks, redelivery, and correlation from existing
infrastructure — while billing is untouched, because NATS only carries
envelopes, never model calls.

Subjects: ``sc.mesh.<node>`` for direct dispatch, ``sc.mesh.broadcast`` for
fan-out. JetStream stream ``SC_MESH`` persists them at-least-once; consumers
ack explicitly, unacked messages redeliver.

Requires ``nats-py`` (``pip install nats-py``) — import is guarded so the
rest of the SDK never depends on it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from sc_envelope import ENVELOPE_MAX_AGE_S, Envelope

__version__ = "0.12.0"

DEFAULT_URL = "nats://localhost:4222"
STREAM_NAME = "SC_MESH"
SUBJECT_PREFIX = "sc.mesh"


class NatsUnavailable(RuntimeError):
    pass


def _require_nats():
    try:
        import nats
        return nats
    except ImportError as exc:
        raise NatsUnavailable(
            "nats-py is not installed — `pip install nats-py` to use the mesh bus"
        ) from exc


def subject_for(node: str) -> str:
    return f"{SUBJECT_PREFIX}.{node}"


class MeshBus:
    """Thin async wrapper: signed-envelope publish/subscribe with JetStream
    at-least-once delivery. One instance per process."""

    def __init__(self, key: bytes, url: str = DEFAULT_URL):
        self.key = key
        self.url = url
        self._nc = None
        self._js = None

    async def connect(self) -> MeshBus:
        nats = _require_nats()
        self._nc = await nats.connect(self.url)
        self._js = self._nc.jetstream()
        try:
            await self._js.add_stream(name=STREAM_NAME,
                                      subjects=[f"{SUBJECT_PREFIX}.>"])
        except Exception:
            pass  # stream already exists
        return self

    async def close(self) -> None:
        if self._nc is not None:
            await self._nc.drain()
            self._nc = None
            self._js = None

    def _check(self) -> None:
        if self._js is None:
            raise NatsUnavailable("MeshBus not connected — call connect() first")

    async def publish(self, envelope: Envelope) -> None:
        """Sign and publish; awaits the JetStream ack (durable write)."""
        self._check()
        envelope.sign(self.key)
        await self._js.publish(subject_for(envelope.recipient),
                               envelope.to_json().encode("utf-8"))

    async def subscribe(self, node: str,
                        handler: Callable[[Envelope], Awaitable[None]],
                        durable: str = "", require_valid: bool = True) -> None:
        """Durable push subscription for one node. Messages are acked only
        after the handler returns — a crash mid-handling redelivers."""
        self._check()
        key = self.key

        async def _on_msg(msg):
            try:
                env = Envelope.from_json(msg.data.decode("utf-8"))
            except Exception:
                await msg.term()  # malformed — never redeliver
                return
            if require_valid and not env.verify(key, max_age_s=ENVELOPE_MAX_AGE_S):
                await msg.term()  # forged/unsigned/stale — drop, don't retry
                return
            await handler(env)
            await msg.ack()

        await self._js.subscribe(subject_for(node), durable=durable or f"sc-{node}",
                                 cb=_on_msg, manual_ack=True)

    async def request(self, envelope: Envelope, timeout: float = 10.0) -> Envelope:
        """Core-NATS request/reply (built-in correlation via reply inbox)."""
        self._check()
        envelope.sign(self.key)
        msg = await self._nc.request(subject_for(envelope.recipient),
                                     envelope.to_json().encode("utf-8"),
                                     timeout=timeout)
        reply = Envelope.from_json(msg.data.decode("utf-8"))
        if not reply.verify(self.key, max_age_s=ENVELOPE_MAX_AGE_S):
            raise NatsUnavailable("reply failed signature verification or is stale")
        return reply


def run_sync(coro):
    """Convenience for scripts that aren't async."""
    return asyncio.get_event_loop().run_until_complete(coro)


__all__ = [
    "DEFAULT_URL",
    "STREAM_NAME",
    "SUBJECT_PREFIX",
    "MeshBus",
    "NatsUnavailable",
    "run_sync",
    "subject_for",
]
