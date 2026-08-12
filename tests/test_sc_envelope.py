"""Unit tests for sc_envelope — signing, tamper detection, agent cards."""

import time

import pytest
import sc_envelope
import sc_windows_credentials
from sc_envelope import (
    AgentCard,
    Envelope,
    EnvelopeError,
    EnvelopeReplayStore,
    load_cards,
    load_or_create_mesh_key,
    publish_card,
)


@pytest.fixture
def key(tmp_path):
    return load_or_create_mesh_key(tmp_path / "mesh.key", allow_plaintext_file=True)


def test_key_created_once_and_reloaded(tmp_path):
    path = tmp_path / "k" / "mesh.key"
    k1 = load_or_create_mesh_key(path, allow_plaintext_file=True)
    k2 = load_or_create_mesh_key(path, allow_plaintext_file=True)
    assert k1 == k2
    assert len(k1) == 32


def test_invalid_length_legacy_key_is_preserved_during_credential_migration(tmp_path, monkeypatch):
    legacy = tmp_path / "mesh.key"
    legacy.write_text((b"short").hex(), encoding="utf-8")
    writes = []
    monkeypatch.setattr(sc_envelope, "DEFAULT_KEY_PATH", legacy)
    monkeypatch.setattr(sc_envelope.os, "name", "nt")
    monkeypatch.setattr(
        sc_windows_credentials, "write_secret", lambda target, value: writes.append((target, value))
    )
    monkeypatch.setattr(sc_windows_credentials, "read_secret", lambda _target: None)

    with pytest.raises(EnvelopeError, match="exactly 32 bytes"):
        load_or_create_mesh_key()

    assert legacy.exists()
    assert writes == []


def test_sign_and_verify_roundtrip(key, tmp_path):
    store = EnvelopeReplayStore(tmp_path / "replay.sqlite3")
    env = Envelope(sender="windows-a", recipient="spark-1", kind="task.dispatch",
                   payload={"prompt": "do it"}, correlation_id="abc123")
    env.sign(key)
    assert env.verify(key, replay_store=store)

    parsed = Envelope.from_json(env.to_json())
    assert not parsed.verify(key, replay_store=store)
    assert parsed.correlation_id == "abc123"
    assert parsed.payload == {"prompt": "do it"}


def test_tampered_payload_fails_verification(key):
    env = Envelope(sender="a", recipient="b", kind="ping").sign(key)
    env.payload["injected"] = "evil"
    assert not env.verify(key)


def test_unsigned_and_wrong_key_fail(key):
    env = Envelope(sender="a", recipient="b", kind="ping")
    assert not env.verify(key)
    env.sign(key)
    assert not env.verify(b"\x00" * 32)


def test_freshness_and_durable_replay_are_mandatory(key, tmp_path):
    replay_path = tmp_path / "replay.sqlite3"
    now = time.time()
    stale = Envelope(sender="a", recipient="b", kind="ping", ts=now - 301).sign(key)
    assert not stale.verify(key, replay_store=EnvelopeReplayStore(replay_path), now=now)

    future = Envelope(sender="a", recipient="b", kind="ping", ts=now + 6).sign(key)
    assert not future.verify(key, replay_store=EnvelopeReplayStore(replay_path), now=now)

    fresh = Envelope(sender="a", recipient="b", kind="ping", ts=now).sign(key)
    assert fresh.verify(key, replay_store=EnvelopeReplayStore(replay_path), now=now)
    assert not Envelope.from_json(fresh.to_json()).verify(
        key, replay_store=EnvelopeReplayStore(replay_path), now=now + 1
    )


@pytest.mark.parametrize("max_age", [0, -1, float("nan"), float("inf"), True])
def test_replay_window_cannot_be_disabled(key, tmp_path, max_age):
    env = Envelope(sender="a", recipient="b", kind="ping").sign(key)
    assert not env.verify(
        key,
        max_age_s=max_age,
        replay_store=EnvelopeReplayStore(tmp_path / "replay.sqlite3"),
    )


def test_malformed_json_raises(key):
    with pytest.raises(EnvelopeError):
        Envelope.from_json("{broken")
    with pytest.raises(EnvelopeError):
        Envelope.from_json('{"unexpected": "fields only"}')


def test_agent_card_publish_load_and_fail_closed(tmp_path, key):
    hub = tmp_path / "hub"
    good = AgentCard(name="windows-a", node="windows-a",
                     capabilities=["terminal.inject.chat", "capture"],
                     endpoints={"agent_port": 9877}).sign(key)
    publish_card(good, hub)

    forged = AgentCard(name="mallory", node="evil", capabilities=["*"])
    forged.sig = "00" * 32  # bogus signature
    publish_card(forged, hub)

    cards = load_cards(hub, key)
    assert [c.name for c in cards] == ["windows-a"]  # forged card dropped

    all_cards = load_cards(hub, key=None)
    assert len(all_cards) == 2  # without a key, no verification requested


def test_card_tamper_detected(key):
    card = AgentCard(name="a", node="n", capabilities=["x"]).sign(key)
    assert card.verify(key)
    card.capabilities.append("terminal.inject.shell")
    assert not card.verify(key)


def test_agent_card_freshness_is_mandatory(key):
    now = time.time()
    stale = AgentCard(name="stale", node="n", issued_at=now - 301).sign(key)
    future = AgentCard(name="future", node="n", issued_at=now + 6).sign(key)
    assert not stale.verify(key, now=now)
    assert not future.verify(key, now=now)
    assert not AgentCard(name="disabled", node="n", issued_at=now).sign(key).verify(
        key, max_age_s=0, now=now
    )
