from __future__ import annotations

import base64
import json
import time

import pytest
from sc_identity import AgentIdentity
from sc_migration import (
    _authorized_cli_store,
    _canonical,
    create_signed_manifest,
    enroll_migration_signer,
    manifest_consumed,
    verify_migration_manifest,
)
from sc_seat_identity import (
    create_challenge,
    create_enrollment,
    create_proof,
    deliver_challenge_postmessage,
    enroll_receiver_key,
    secure_channel_evidence,
    tab_snapshot_digest,
)
from self_connect import (
    AgentRegistry,
    Checkpoint,
    MigrationCoordinator,
    write_checkpoint,
)


def _binding(hwnd: int = 9001) -> dict:
    return {
        "hwnd": hwnd, "pid": 123, "exe_name": "cmd.exe",
        "class_name": "ConsoleWindowClass", "process_started_at": 100.0,
        "binding_sha256": "binding-digest",
    }


def _manifest(tmp_path, *, now=None, ttl=120):
    identity = AgentIdentity.generate("migration-authority")
    trust = enroll_migration_signer(identity, tmp_path / "trust.json")
    checkpoint = write_checkpoint(
        Checkpoint(role="B", own_hwnd=42, peers=[], pending={"work": "resume"}, meta={}),
        str(tmp_path / "checkpoint.json"),
    )
    manifest = create_signed_manifest(
        identity=identity, checkpoint_path=checkpoint, role="B", source_hwnd=42,
        successor_binding=_binding(), output_path=tmp_path / "manifest.json",
        now=now, ttl_seconds=ttl,
    )
    return identity, trust, checkpoint, manifest


def _seat_bundle(identity, manifest, *, now=None):
    manifest = __import__("pathlib").Path(manifest)
    current = time.time() if now is None else now
    seat = AgentIdentity.generate("seat")
    receiver = AgentIdentity.generate("receiver")
    receiver_trust = enroll_receiver_key(
        receiver, manifest.with_suffix(".receiver-trust.json")
    )
    enrollment = create_enrollment(
        seat_identity=seat, authority_identity=identity, birth_id="seat-a",
        generation=1, now=current,
    )
    issue_store = manifest.with_suffix(".seat-issued.sqlite3")
    snapshot = {"ok": True, "tab_runtime_id": [1, 2],
                "term_control_runtime_id": [1, 3], "peer_birth_id": "seat-a"}
    operation = __import__("hashlib").sha256(
        json.dumps(json.loads(manifest.read_text(encoding="utf-8")), sort_keys=True,
                   separators=(",", ":")).encode()
    ).hexdigest()
    challenge = create_challenge(
        enrollment=enrollment, operation_sha256=operation,
        tab_snapshot_sha256=tab_snapshot_digest(snapshot), response_address_sha256="33" * 32,
        server_nonce="44" * 32, authority_identity=identity,
        expected_peer_sid="S-1-5-21-test", expected_pipe_instance="pipe-1",
        issue_store=issue_store, now=current,
    )
    delivery = deliver_challenge_postmessage(
        challenge=challenge, target_hwnd=9001, authority_identity=identity,
        sender=lambda hwnd, text: {"ok": True, "transport": "postmessage_wm_char",
                                   "chars_accepted": len(text), "target_hwnd": hwnd},
        tab_checkpoint=lambda stage: {**snapshot, "stage": stage}, now=current,
    )
    bundle = {"enrollment": enrollment, "challenge": challenge, "delivery": delivery,
            "proof": create_proof(seat_identity=seat, challenge=challenge, delivery=delivery,
                                  authority_public_key_hex=identity.public_key_hex,
                                  issue_store=issue_store),
            "channel_evidence": secure_channel_evidence(
                challenge, receiver_identity=receiver, peer_sid="S-1-5-21-test",
                pipe_instance="pipe-1", now=current),
            "receiver_public_key_hex": receiver.public_key_hex}
    bundle["_test_issue_store"] = str(issue_store)
    bundle["_test_snapshot"] = snapshot
    bundle["_test_receiver_trust"] = str(receiver_trust)
    return bundle


def _seat_verify_kwargs(bundle):
    return {
        "seat_issue_store": bundle["_test_issue_store"],
        "seat_receiver_trust_store": bundle["_test_receiver_trust"],
        "seat_tab_snapshot_resolver": lambda _hwnd, birth_id: (
            dict(bundle["_test_snapshot"])
            if birth_id == bundle["enrollment"]["birth_id"]
            else (_ for _ in ()).throw(AssertionError("unverified birth_id"))
        ),
    }


def test_r3_consuming_manifest_without_seat_channel_proof_rejects(tmp_path):
    _identity, trust, _checkpoint, manifest = _manifest(tmp_path)
    with pytest.raises(ValueError, match="per-seat channel proof is required"):
        verify_migration_manifest(
            manifest, expected_hwnd=9001, trust_store=trust,
            replay_store=tmp_path / "replay.sqlite3", consume=True,
            target_resolver=lambda _hwnd: _binding(),
        )


def test_migration_pins_separately_enrolled_receiver_key(tmp_path):
    identity, trust, _checkpoint, manifest = _manifest(tmp_path)
    bundle = _seat_bundle(identity, manifest)
    rogue = AgentIdentity.generate("rogue-receiver")
    bundle["receiver_public_key_hex"] = rogue.public_key_hex
    bundle["channel_evidence"] = secure_channel_evidence(
        bundle["challenge"], receiver_identity=rogue,
        peer_sid="S-1-5-21-test", pipe_instance="pipe-1",
    )
    with pytest.raises(ValueError, match="not independently trusted"):
        verify_migration_manifest(
            manifest, expected_hwnd=9001, trust_store=trust,
            replay_store=tmp_path / "replay.sqlite3", consume=True,
            target_resolver=lambda _hwnd: _binding(), seat_bundle=bundle,
            seat_replay_store=tmp_path / "seat.sqlite3",
            **_seat_verify_kwargs(bundle),
        )


def test_migration_rejects_authority_key_as_receiver_even_if_enrolled(tmp_path):
    identity, trust, _checkpoint, manifest = _manifest(tmp_path)
    bundle = _seat_bundle(identity, manifest)
    authority_receiver_trust = enroll_receiver_key(
        identity, tmp_path / "authority-receiver-trust.json"
    )
    bundle["channel_evidence"] = secure_channel_evidence(
        bundle["challenge"], receiver_identity=identity,
        peer_sid="S-1-5-21-test", pipe_instance="pipe-1",
    )
    kwargs = _seat_verify_kwargs(bundle)
    kwargs["seat_receiver_trust_store"] = authority_receiver_trust
    with pytest.raises(ValueError, match="distinct from migration authority"):
        verify_migration_manifest(
            manifest, expected_hwnd=9001, trust_store=trust,
            replay_store=tmp_path / "replay.sqlite3", consume=True,
            target_resolver=lambda _hwnd: _binding(), seat_bundle=bundle,
            seat_replay_store=tmp_path / "seat.sqlite3", **kwargs,
        )


def test_routing_binding_mismatch_is_telemetry_not_authorization(tmp_path):
    identity, trust, _checkpoint, manifest = _manifest(tmp_path)
    bundle = _seat_bundle(identity, manifest)
    result = verify_migration_manifest(
        manifest, expected_hwnd=9001, trust_store=trust,
        replay_store=tmp_path / "replay.sqlite3", consume=True,
        target_resolver=lambda _hwnd: {**_binding(), "pid": 999},
        seat_bundle=bundle, seat_replay_store=tmp_path / "seat.sqlite3",
        **_seat_verify_kwargs(bundle),
    )
    assert result["ok"] is True
    assert result["status"] == "ACCEPTED"
    assert result["routing_binding_matches"] is False


def test_signed_manifest_requires_trusted_authority_exact_target_and_one_time_use(tmp_path):
    identity, trust, _checkpoint, manifest = _manifest(tmp_path)
    replay = tmp_path / "replay.sqlite3"
    bundle = _seat_bundle(identity, manifest)
    result = verify_migration_manifest(
        manifest, expected_hwnd=9001, trust_store=trust, replay_store=replay,
        consume=True, target_resolver=lambda _hwnd: _binding(), seat_bundle=bundle,
        seat_replay_store=tmp_path / "seat.sqlite3",
        **_seat_verify_kwargs(bundle),
    )
    assert result["status"] == "ACCEPTED"
    with pytest.raises(ValueError, match="already been consumed"):
        verify_migration_manifest(
            manifest, expected_hwnd=9001, trust_store=trust, replay_store=replay,
            consume=True, target_resolver=lambda _hwnd: _binding(), seat_bundle=bundle,
            seat_replay_store=tmp_path / "seat.sqlite3",
            **_seat_verify_kwargs(bundle),
        )
    with pytest.raises(ValueError, match="different successor HWND"):
        verify_migration_manifest(
            manifest, expected_hwnd=9002, trust_store=trust,
            target_resolver=lambda _hwnd: _binding(9002),
        )


def test_tampered_manifest_checkpoint_untrusted_signer_and_expiry_fail_closed(tmp_path):
    _identity, trust, checkpoint, manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["role"] = "attacker"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="signature is invalid"):
        verify_migration_manifest(
            manifest, expected_hwnd=9001, trust_store=trust,
            target_resolver=lambda _hwnd: _binding(),
        )

    _identity2, trust2, checkpoint2, manifest2 = _manifest(tmp_path / "second")
    with pytest.raises(ValueError, match="independently trusted"):
        verify_migration_manifest(
            manifest2, expected_hwnd=9001, trust_store=trust,
            target_resolver=lambda _hwnd: _binding(),
        )
    with open(checkpoint2, "ab") as handle:
        handle.write(b" ")
    with pytest.raises(ValueError, match="checkpoint bytes"):
        verify_migration_manifest(
            manifest2, expected_hwnd=9001, trust_store=trust2,
            target_resolver=lambda _hwnd: _binding(),
        )

    past = time.time() - 500
    _identity3, trust3, _checkpoint3, manifest3 = _manifest(tmp_path / "third", now=past, ttl=10)
    with pytest.raises(ValueError, match="not currently valid"):
        verify_migration_manifest(
            manifest3, expected_hwnd=9001, trust_store=trust3,
            target_resolver=lambda _hwnd: _binding(),
        )


def test_coordinator_never_spawns_without_explicit_authority_and_live_opt_in(
    tmp_path, monkeypatch
):
    spawn_calls = []
    monkeypatch.setattr(
        "self_connect._subprocess.Popen",
        lambda *args, **kwargs: spawn_calls.append((args, kwargs)),
    )
    coordinator = MigrationCoordinator(
        own_hwnd=42, role="B", registry=AgentRegistry(),
        checkpoint_path=str(tmp_path / "checkpoint.json"), capacity=100, threshold=.7,
    )
    with pytest.raises(RuntimeError, match="identity is required"):
        coordinator.tick(75)

    identity = AgentIdentity.generate("not-enrolled")
    coordinator = MigrationCoordinator(
        own_hwnd=42, role="B", registry=AgentRegistry(),
        checkpoint_path=str(tmp_path / "checkpoint2.json"), capacity=100, threshold=.7,
        migration_identity=identity,
    )
    with pytest.raises(RuntimeError, match="explicit allow_live_spawn"):
        coordinator.tick(75)
    assert spawn_calls == []


def test_coordinator_sends_one_line_and_waits_for_verified_consumption(tmp_path):
    identity = AgentIdentity.generate("authority")
    trust = enroll_migration_signer(identity, tmp_path / "trust.json")
    replay = tmp_path / "replay.sqlite3"
    notices = []

    def accept(hwnd, notice):
        notices.append(notice)
        manifest = json.loads(notice.split("--manifest ", 1)[1].split(" --expected-hwnd", 1)[0])
        bundle = _seat_bundle(identity, manifest)
        verify_migration_manifest(
            manifest, expected_hwnd=hwnd, trust_store=trust, replay_store=replay,
            consume=True, target_resolver=lambda _hwnd: _binding(),
            seat_bundle=bundle, seat_replay_store=tmp_path / "seat.sqlite3",
            **_seat_verify_kwargs(bundle),
        )

    coordinator = MigrationCoordinator(
        own_hwnd=42, role="B", registry=AgentRegistry(),
        checkpoint_path=str(tmp_path / "checkpoint.json"), capacity=100, threshold=.7,
        migration_identity=identity, migration_trust_store=str(trust),
        migration_replay_store=str(replay), terminal_factory=lambda: 9001,
        briefing_sender=accept, target_resolver=lambda _hwnd: _binding(),
        verification_wait_seconds=0,
    )
    assert coordinator.tick(75, pending={"work": "resume"}) is True
    assert coordinator.has_migrated is True
    assert len(notices) == 1
    assert "\n" not in notices[0] and "\r" not in notices[0]
    assert "do not accept the role" in notices[0]
    assert f"--trust-store {json.dumps(str(trust))}" in notices[0]
    assert f"--replay-store {json.dumps(str(replay))}" in notices[0]


def test_inverted_validity_window_is_rejected_even_when_signed(tmp_path):
    identity, trust, _checkpoint, manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["expires_at"] = data["issued_at"]
    body = {key: value for key, value in data.items() if key != "signature_b64"}
    data["signature_b64"] = base64.b64encode(identity.sign(_canonical(body))).decode()
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="inverted or empty"):
        verify_migration_manifest(
            manifest, expected_hwnd=9001, trust_store=trust,
            target_resolver=lambda _hwnd: _binding(),
        )


def test_consumption_requires_receipt_bound_to_exact_manifest(tmp_path):
    identity, trust, _checkpoint, manifest = _manifest(tmp_path)
    replay = tmp_path / "replay.sqlite3"
    bundle = _seat_bundle(identity, manifest)
    verify_migration_manifest(
        manifest, expected_hwnd=9001, trust_store=trust, replay_store=replay,
        consume=True, target_resolver=lambda _hwnd: _binding(), seat_bundle=bundle,
        seat_replay_store=tmp_path / "seat.sqlite3",
        **_seat_verify_kwargs(bundle),
    )
    manifest_id = json.loads(manifest.read_text(encoding="utf-8"))["manifest_id"]
    assert manifest_consumed(manifest_id, replay, manifest_path=manifest)
    changed = json.loads(manifest.read_text(encoding="utf-8"))
    changed["nonce"] = "0" * 32
    manifest.write_text(json.dumps(changed), encoding="utf-8")
    assert not manifest_consumed(manifest_id, replay, manifest_path=manifest)


def test_custom_cli_stores_require_launch_authorization(tmp_path, monkeypatch):
    trust = str(tmp_path / "trust.json")
    with pytest.raises(ValueError, match="not authorized"):
        _authorized_cli_store(trust, "SELFCONNECT_MIGRATION_TRUST_STORE", "trust store")
    monkeypatch.setenv("SELFCONNECT_MIGRATION_TRUST_STORE", trust)
    assert _authorized_cli_store(
        trust, "SELFCONNECT_MIGRATION_TRUST_STORE", "trust store"
    ) == trust


def test_coordinator_rechecks_exact_binding_before_sender(tmp_path):
    identity = AgentIdentity.generate("authority")
    trust = enroll_migration_signer(identity, tmp_path / "trust.json")
    bindings = [_binding(), {**_binding(), "pid": 999}]
    sent = []

    def resolver(_hwnd):
        return bindings.pop(0)

    coordinator = MigrationCoordinator(
        own_hwnd=42, role="B", registry=AgentRegistry(),
        checkpoint_path=str(tmp_path / "checkpoint.json"), capacity=100, threshold=.7,
        migration_identity=identity, migration_trust_store=str(trust),
        terminal_factory=lambda: 9001, briefing_sender=lambda *_args: sent.append(True),
        target_resolver=resolver, verification_wait_seconds=0,
    )
    with pytest.raises(RuntimeError, match="binding changed"):
        coordinator.tick(75)
    assert sent == []
    assert coordinator.has_migrated is False
