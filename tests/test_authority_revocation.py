from __future__ import annotations

import json

import pytest
import sc_authority_trust
import sc_seat_identity
from sc_authority_trust import (
    _bootstrap_local_authority_trust_for_test,
    _local_authority_public_key,
    apply_authority_transition,
    authority_public_key,
    build_authority_transition,
    load_local_authority_trust,
    sign_authority_transition,
)
from sc_identity import AgentIdentity
from sc_seat_identity import key_id
from sc_seat_revocation import (
    _resolve_local_revoked_key_ids,
    apply_revocation_snapshot,
    create_revocation_snapshot,
    resolve_revoked_key_ids,
    sign_revocation_snapshot,
)
from sc_trust_anchor import HighAssuranceUnavailable, _anchor_path


def _trust(tmp_path):
    roots = [AgentIdentity.generate("root-a"), AgentIdentity.generate("root-b")]
    recovery = [AgentIdentity.generate("recovery-a"), AgentIdentity.generate("recovery-b")]
    path = tmp_path / "authority.json"
    _bootstrap_local_authority_trust_for_test(
        path,
        root_public_keys=[item.public_key_hex for item in roots],
        quorum=2,
        recovery_public_keys=[item.public_key_hex for item in recovery],
        recovery_quorum=2,
    )
    return path, roots, recovery


def test_existing_root_cannot_self_enroll_or_rotate_with_one_key(tmp_path):
    path, roots, _recovery = _trust(tmp_path)
    with pytest.raises(FileExistsError):
        _bootstrap_local_authority_trust_for_test(
            path,
            root_public_keys=[roots[0].public_key_hex],
            quorum=1,
            recovery_public_keys=[roots[0].public_key_hex],
            recovery_quorum=1,
        )
    replacement = AgentIdentity.generate("replacement")
    transition = build_authority_transition(path, new_root_public_keys=[replacement.public_key_hex], new_quorum=1)
    with pytest.raises(ValueError, match="quorum"):
        apply_authority_transition(path, transition, [sign_authority_transition(transition, roots[0])])


def test_rotation_and_recovery_require_configured_quorums_and_reject_stale_transition(tmp_path):
    path, roots, recovery = _trust(tmp_path)
    next_roots = [AgentIdentity.generate("next-a"), AgentIdentity.generate("next-b")]
    rotation = build_authority_transition(
        path,
        new_root_public_keys=[item.public_key_hex for item in next_roots],
        new_quorum=2,
    )
    signatures = [sign_authority_transition(rotation, item) for item in roots]
    state = apply_authority_transition(path, rotation, signatures)
    assert state["version"] == 2 and state["epoch"] == 1
    with pytest.raises(ValueError, match="stale"):
        apply_authority_transition(path, rotation, signatures)
    recovered = AgentIdentity.generate("recovered")
    transition = build_authority_transition(
        path,
        new_root_public_keys=[recovered.public_key_hex],
        new_quorum=1,
        recovery=True,
    )
    state = apply_authority_transition(
        path,
        transition,
        [sign_authority_transition(transition, item) for item in recovery],
    )
    assert state["epoch"] == 2 and _local_authority_public_key(path, key_id(recovered.public_key_hex))


def test_signed_history_detects_direct_root_replacement(tmp_path):
    path, roots, _recovery = _trust(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    rogue = AgentIdentity.generate("rogue")
    data["roots"] = {key_id(rogue.public_key_hex): rogue.public_key_hex}
    data["quorum"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="signed history"):
        load_local_authority_trust(path)


def test_authority_raw_file_restore_is_rejected_across_restart(tmp_path):
    path, roots, _recovery = _trust(tmp_path)
    old_bytes = path.read_bytes()
    replacement = AgentIdentity.generate("replacement")
    transition = build_authority_transition(path, new_root_public_keys=[replacement.public_key_hex], new_quorum=1)
    apply_authority_transition(path, transition, [sign_authority_transition(transition, item) for item in roots])
    path.write_bytes(old_bytes)
    with pytest.raises(ValueError, match="rollback detected"):
        load_local_authority_trust(path)


def test_revocation_snapshot_is_signed_fresh_and_monotonic(tmp_path):
    trust, roots, _recovery = _trust(tmp_path)
    store = tmp_path / "revocations.json"
    revoked = key_id(AgentIdentity.generate("seat").public_key_hex)
    snapshot = create_revocation_snapshot(store, trust, [revoked], now=1000.0, ttl_seconds=30)
    signatures = [sign_revocation_snapshot(snapshot, item) for item in roots]
    apply_revocation_snapshot(store, trust, snapshot, signatures, now=1001.0)
    assert _resolve_local_revoked_key_ids(store, trust, now=1002.0) == frozenset({revoked})
    with pytest.raises(ValueError, match="replay or rollback"):
        apply_revocation_snapshot(store, trust, snapshot, signatures, now=1002.0)
    with pytest.raises(ValueError, match="stale"):
        _resolve_local_revoked_key_ids(store, trust, now=1031.0)


def test_revocation_raw_file_restore_is_rejected_across_restart(tmp_path):
    trust, roots, _recovery = _trust(tmp_path)
    store = tmp_path / "revocations.json"
    first = create_revocation_snapshot(store, trust, [], now=1000.0)
    apply_revocation_snapshot(
        store,
        trust,
        first,
        [sign_revocation_snapshot(first, item) for item in roots],
        now=1000.0,
    )
    old_bytes = store.read_bytes()
    second = create_revocation_snapshot(store, trust, [], now=1001.0)
    apply_revocation_snapshot(
        store,
        trust,
        second,
        [sign_revocation_snapshot(second, item) for item in roots],
        now=1001.0,
    )
    store.write_bytes(old_bytes)
    with pytest.raises(ValueError, match="rollback detected"):
        _resolve_local_revoked_key_ids(store, trust, now=1002.0)


def test_revocation_snapshot_rejects_partial_ids_and_missing_quorum(tmp_path):
    trust, roots, _recovery = _trust(tmp_path)
    store = tmp_path / "revocations.json"
    with pytest.raises(ValueError, match="invalid revoked"):
        create_revocation_snapshot(store, trust, ["abcd"], now=1000.0)
    snapshot = create_revocation_snapshot(store, trust, [], now=1000.0)
    with pytest.raises(ValueError, match="quorum"):
        apply_revocation_snapshot(
            store,
            trust,
            snapshot,
            [sign_revocation_snapshot(snapshot, roots[0])],
            now=1001.0,
        )


def test_runtime_revocation_resolver_requires_a_snapshot(tmp_path):
    trust, _roots, _recovery = _trust(tmp_path)
    with pytest.raises(HighAssuranceUnavailable, match="separately privileged"):
        resolve_revoked_key_ids(tmp_path / "missing.json", trust, now=1000.0)


def test_public_python_apis_cannot_mint_or_reset_trust_roots():
    assert not hasattr(sc_authority_trust, "bootstrap_authority_trust")
    assert not hasattr(sc_seat_identity, "enroll_receiver_key")


def test_paired_same_user_state_and_dpapi_journal_rollback_never_becomes_authority(tmp_path):
    path, roots, _recovery = _trust(tmp_path)
    old_state = path.read_bytes()
    anchor = _anchor_path(path)
    old_anchor = anchor.read_bytes()
    replacement = AgentIdentity.generate("replacement")
    transition = build_authority_transition(
        path,
        new_root_public_keys=[replacement.public_key_hex],
        new_quorum=1,
    )
    apply_authority_transition(
        path,
        transition,
        [sign_authority_transition(transition, item) for item in roots],
    )

    # Exact hostile-same-user PoC: roll back both mutable files together.
    path.write_bytes(old_state)
    anchor.write_bytes(old_anchor)

    # Local integrity cannot detect paired rollback and does not claim it can.
    assert load_local_authority_trust(path)["version"] == 1
    with pytest.raises(HighAssuranceUnavailable, match="current-user DPAPI"):
        authority_public_key(path, key_id(roots[0].public_key_hex))


def test_paired_revocation_state_and_journal_rollback_never_becomes_authority(tmp_path):
    trust, roots, _recovery = _trust(tmp_path)
    store = tmp_path / "revocations.json"
    first = create_revocation_snapshot(store, trust, [], now=1000.0)
    apply_revocation_snapshot(
        store,
        trust,
        first,
        [sign_revocation_snapshot(first, item) for item in roots],
        now=1000.0,
    )
    old_state = store.read_bytes()
    anchor = _anchor_path(store)
    old_anchor = anchor.read_bytes()
    revoked = key_id(AgentIdentity.generate("seat").public_key_hex)
    second = create_revocation_snapshot(store, trust, [revoked], now=1001.0)
    apply_revocation_snapshot(
        store,
        trust,
        second,
        [sign_revocation_snapshot(second, item) for item in roots],
        now=1001.0,
    )
    store.write_bytes(old_state)
    anchor.write_bytes(old_anchor)

    assert _resolve_local_revoked_key_ids(store, trust, now=1002.0) == frozenset()
    with pytest.raises(HighAssuranceUnavailable, match="separately privileged"):
        resolve_revoked_key_ids(store, trust, now=1002.0)
