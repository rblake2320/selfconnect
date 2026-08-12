from __future__ import annotations

import copy

import pytest
from sc_identity import AgentIdentity
from sc_seat_identity import (
    create_challenge,
    create_enrollment,
    create_proof,
    deliver_challenge_postmessage,
    key_id,
    secure_channel_evidence,
    tab_snapshot_digest,
    verify_proof,
)


def _case(tmp_path, *, birth="seat-a", now=1000.0):
    authority = AgentIdentity.generate("authority")
    receiver = authority
    seat = AgentIdentity.generate(birth)
    enrollment = create_enrollment(seat_identity=seat, authority_identity=authority,
                                   birth_id=birth, generation=1, now=now)
    issue_store = tmp_path / f"{birth}-issued.sqlite3"
    live_snapshot = {"ok": True, "tab_runtime_id": [7, 11],
                     "term_control_runtime_id": [7, 19], "peer_birth_id": birth}
    challenge = create_challenge(
        enrollment=enrollment, operation_sha256="11" * 32,
        tab_snapshot_sha256=tab_snapshot_digest(live_snapshot),
        response_address_sha256="33" * 32, server_nonce="44" * 32,
        expected_peer_sid="S-1-5-21-test", expected_pipe_instance="pipe-1",
        authority_identity=authority, issue_store=issue_store, now=now,
    )
    checkpoints = []
    delivery = deliver_challenge_postmessage(
        challenge=challenge, target_hwnd=123, authority_identity=authority,
        sender=lambda hwnd, text: {"ok": True, "transport": "postmessage_wm_char",
                                   "chars_accepted": len(text), "target_hwnd": hwnd},
        tab_checkpoint=lambda stage: checkpoints.append(stage) or {**live_snapshot, "stage": stage}, now=now,
    )
    proof = create_proof(seat_identity=seat, challenge=challenge, delivery=delivery,
                         authority_public_key_hex=authority.public_key_hex, issue_store=issue_store)
    channel = secure_channel_evidence(challenge, receiver_identity=receiver,
                                      peer_sid="S-1-5-21-test", pipe_instance="pipe-1", now=now)
    kwargs = {"challenge": challenge, "delivery": delivery, "enrollment": enrollment,
              "channel_evidence": channel, "authority_public_key_hex": authority.public_key_hex,
              "receiver_public_key_hex": receiver.public_key_hex,
              "expected_operation_sha256": "11" * 32,
              "expected_tab_snapshot_sha256": tab_snapshot_digest(live_snapshot),
              "expected_target_hwnd": 123,
              "replay_store": tmp_path / f"{birth}.sqlite3", "issue_store": issue_store,
              "now": now + 1}
    return authority, receiver, seat, enrollment, challenge, delivery, proof, kwargs, checkpoints


def test_r0_verify_proof_rejects_enrollment_not_signed_by_pinned_authority(tmp_path):
    authority, _r, seat, enrollment, challenge, delivery, _proof, kwargs, _ = _case(tmp_path)
    rogue = AgentIdentity.generate("rogue-authority")
    rogue_enrollment = create_enrollment(seat_identity=seat, authority_identity=rogue,
                                         birth_id="seat-a", generation=1, now=1000)
    with pytest.raises(ValueError, match="enrollment signature"):
        verify_proof({}, **{**kwargs, "enrollment": rogue_enrollment})
    assert authority.public_key_hex != rogue.public_key_hex and enrollment != rogue_enrollment


def test_r1_two_seats_have_distinct_crypto_identity_without_hwnd_authority(tmp_path):
    a = _case(tmp_path, birth="seat-a")
    b = _case(tmp_path, birth="seat-b")
    assert key_id(a[2].public_key_hex) != key_id(b[2].public_key_hex)
    assert all(field not in a[3] for field in ("hwnd", "pid", "title", "runtime_id"))
    assert all(field not in a[4] for field in ("hwnd", "pid", "title", "runtime_id"))


def test_r2_same_windows_terminal_host_fails_without_seat_proof(tmp_path):
    *_prefix, kwargs, _checkpoints = _case(tmp_path)
    with pytest.raises(ValueError, match="seat proof signature"):
        verify_proof({}, **kwargs)


def test_postmessage_exact_h_delivery_and_signed_offscreen_channel(tmp_path):
    *_prefix, proof, kwargs, checkpoints = _case(tmp_path)
    result = verify_proof(proof, **kwargs)
    assert result["status"] == "ACCEPTED"
    assert checkpoints == ["before_seat_challenge", "after_seat_challenge"]
    assert kwargs["channel_evidence"]["transport"] == "private_named_pipe_v1"


def test_receiver_channel_signature_and_replay_are_enforced(tmp_path):
    *_prefix, proof, kwargs, _ = _case(tmp_path)
    forged = copy.deepcopy(kwargs["channel_evidence"])
    forged["peer_sid"] = "S-1-5-21-attacker"
    with pytest.raises(ValueError, match="channel signature"):
        verify_proof(proof, **{**kwargs, "channel_evidence": forged})
    verify_proof(proof, **kwargs)
    with pytest.raises(ValueError, match="already been consumed"):
        verify_proof(proof, **kwargs)


def test_unsigned_challenge_and_plus_five_second_forward_skew_rejected(tmp_path):
    *_prefix, proof, kwargs, _ = _case(tmp_path)
    unsigned = dict(kwargs["challenge"])
    unsigned.pop("authority_signature_b64")
    with pytest.raises(ValueError, match="not durably issued"):
        verify_proof(proof, **{**kwargs, "challenge": unsigned})
    authority = _prefix[0]
    future_store = tmp_path / "future-issued.sqlite3"
    future = create_challenge(
        enrollment=_prefix[3], operation_sha256="11" * 32,
        tab_snapshot_sha256=kwargs["expected_tab_snapshot_sha256"],
        response_address_sha256="33" * 32, server_nonce="44" * 32,
        expected_peer_sid="S-1-5-21-test", expected_pipe_instance="pipe-1",
        authority_identity=authority, issue_store=future_store,
        now=kwargs["now"] + 5.001, ttl_seconds=10,
    )
    delivery = deliver_challenge_postmessage(
        challenge=future, target_hwnd=123, authority_identity=authority,
        sender=lambda hwnd, text: {"ok": True, "transport": "postmessage_wm_char",
                                   "chars_accepted": len(text), "target_hwnd": hwnd},
        tab_checkpoint=lambda _stage: {"ok": True, "tab_runtime_id": [7, 11],
                                       "term_control_runtime_id": [7, 19],
                                       "peer_birth_id": "seat-a"}, now=kwargs["now"],
    )
    future_proof = create_proof(
        seat_identity=_prefix[2], challenge=future, delivery=delivery,
        authority_public_key_hex=authority.public_key_hex, issue_store=future_store,
    )
    with pytest.raises(ValueError, match="not currently valid"):
        verify_proof(future_proof, **{**kwargs, "challenge": future, "delivery": delivery,
                                      "issue_store": future_store})


@pytest.mark.parametrize("field", ["operation_sha256", "tab_snapshot_sha256"])
def test_exact_expected_digest_recomputed(field, tmp_path):
    *_prefix, proof, kwargs, _ = _case(tmp_path)
    changed = {**kwargs, f"expected_{field}": "aa" * 32}
    with pytest.raises(ValueError, match="expected digest"):
        verify_proof(proof, **changed)


def test_revocation_and_private_key_absence(tmp_path):
    *_prefix, proof, kwargs, _ = _case(tmp_path)
    seat_key = kwargs["enrollment"]["seat_key_id"]
    with pytest.raises(ValueError, match="revoked"):
        verify_proof(proof, **kwargs, revoked_key_ids=frozenset({seat_key}))
    assert "private" not in repr((kwargs["enrollment"], kwargs["challenge"], proof)).lower()
