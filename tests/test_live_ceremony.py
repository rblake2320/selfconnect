from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sc_live_ceremony as ceremony
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class ProtocolRejected(ValueError):
    pass


_PRIVATE_KEY = Ed25519PrivateKey.generate()
_TRUST = ceremony.EvidenceTrust(
    coordinator_id="codex-12-4abf6b40",
    key_id="coordinator-evidence-key-1",
    public_key=_PRIVATE_KEY.public_key().public_bytes_raw(),
    operator_id="operator-techai",
    operator_authorization_id="live-ceremony-authorization-20260812",
)
_SIGNER = ceremony.EvidenceSigner(trust=_TRUST, sign=_PRIVATE_KEY.sign)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _run(*, checkouts, route, ports, evidence_signer=_SIGNER):
    return ceremony.run_live_ceremony(
        checkouts=checkouts,
        route=route,
        ports=ports,
        evidence_signer=evidence_signer,
    )


class Driver:
    def __init__(self):
        self.old = ceremony.SeatIdentity("seat-old", 3, "old-key", "old-epoch")
        self.new = ceremony.SeatIdentity("seat-new", 4, "new-key", "new-epoch")
        self.sequences = {}

    def _assignment(self, case, seat):
        return {
            "assignment_id": f"assignment-{case}-{seat.birth_id}",
            "payload": f"signed inline payload for {case}",
            "receiver_birth_id": seat.birth_id,
            "receiver_generation": seat.generation,
            "receiver_key_id": seat.key_id,
            "receiver_seat_epoch": seat.seat_epoch,
        }

    def dispatch(self, case, trigger):
        assignment = self._assignment(case, self.old)
        return {
            "assignment": assignment,
            "submit_result": {
                "ok": True,
                "state": "acknowledged",
                "delivery_verified": True,
                "peer_acknowledged": True,
                "decision": "accepted",
                "trigger": trigger,
            },
        }

    def transition(self, assignment, state):
        key = assignment["assignment_id"]
        sequence = self.sequences.get(key, 0) + 1
        self.sequences[key] = sequence
        return {
            "assignment_id": key,
            "state": state,
            "sequence": sequence,
            "nonce": f"receipt-{key}-{sequence}",
        }

    def verify(self, assignment, receipt):
        if receipt["assignment_id"] != assignment["assignment_id"]:
            raise ValueError("wrong assignment")
        return dict(receipt)

    def acknowledge(self, receipt):
        return {
            "assignment_id": receipt["assignment_id"],
            "sequence": receipt["sequence"],
            "receipt_sha256": _digest(receipt),
        }

    def failover(self, assignment, receipt):
        replacement = self._assignment(receipt["state"], self.new)
        return {
            "ok": True,
            "stage": "delivered",
            "operation_id": f"failover-{assignment['assignment_id']}",
            "old_seat": {
                "birth_id": self.old.birth_id,
                "generation": self.old.generation,
                "seat_key_id": self.old.key_id,
                "seat_epoch": self.old.seat_epoch,
            },
            "replacement_seat": {
                "birth_id": self.new.birth_id,
                "generation": self.new.generation,
                "seat_key_id": self.new.key_id,
                "seat_epoch": self.new.seat_epoch,
            },
            "replacement_assignment": replacement,
            "delivery_receipt": {"replacement_assignment_sha256": _digest(replacement)},
            "process_action": "none",
        }

    def negative(self, probe):
        raise ProtocolRejected(f"{probe} rejected")

    def ports(self):
        return ceremony.CeremonyPorts(
            dispatch=self.dispatch,
            seat_transition=self.transition,
            verify_receipt=self.verify,
            acknowledge_receipt=self.acknowledge,
            failover=self.failover,
            replacement_transition=self.transition,
            negative_probe=self.negative,
            protocol_errors=(ProtocolRejected,),
        )


@pytest.fixture
def route():
    return ceremony.ExactRoute(
        hwnd=100,
        pid=200,
        process_start_time_ns=300,
        tab_runtime_id=(42, 1),
        term_control_runtime_id=(42, 2),
        seat=ceremony.SeatIdentity("seat-old", 3, "old-key", "old-epoch"),
    )


@pytest.fixture
def pins(tmp_path, monkeypatch):
    items = []
    for component, filename, char in (
        ("runtime", "sc_assignment_runtime.py", "a"),
        ("trust_pipe", "sc_seat_pipe.py", "b"),
        ("failover", "sc_assignment_failover.py", "c"),
    ):
        path = tmp_path / component
        path.mkdir()
        (path / filename).write_text(f"COMPONENT = {component!r}\n", encoding="utf-8")
        items.append(ceremony.ReviewedCheckout(component, path, char * 40))

    monkeypatch.setattr(ceremony.os, "name", "nt")

    def git(path: Path, *args: str):
        if args == ("rev-parse", "HEAD"):
            return next(item.commit_sha for item in items if item.worktree == path)
        if args == ("status", "--porcelain"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(ceremony, "_git", git)
    return items


def test_two_trigger_live_plan_and_all_negatives(route, pins):
    result = _run(checkouts=pins, route=route, ports=Driver().ports())
    body = result["body"]
    assert [item["trigger"] for item in body["cases"]] == ["blocked", "rejected"]
    assert all(item["replacement_birth_id"] == "seat-new" for item in body["cases"])
    assert len(body["negative_rejections"]) == 7
    assert len(result["body_sha256"]) == 64
    assert "non-authoritative" in body["claim"]
    assert [item["component"] for item in body["reviewed_checkouts"]] == [
        "runtime",
        "trust_pipe",
        "failover",
    ]
    for checkout, evidence in zip(pins, body["reviewed_checkouts"], strict=True):
        assert (
            Path(evidence["module_file"])
            == (checkout.worktree / ceremony._REQUIRED_COMPONENTS[checkout.component]).resolve()
        )
        assert evidence["commit_sha"] == checkout.commit_sha


def test_non_windows_and_missing_or_dirty_pins_fail_closed(route, pins, monkeypatch):
    monkeypatch.setattr(ceremony.os, "name", "posix")
    with pytest.raises(ceremony.CeremonyError, match="Windows-only"):
        _run(checkouts=pins, route=route, ports=Driver().ports())
    monkeypatch.setattr(ceremony.os, "name", "nt")
    with pytest.raises(ceremony.CeremonyError, match="required"):
        _run(checkouts=pins[:2], route=route, ports=Driver().ports())
    monkeypatch.setattr(
        ceremony,
        "_git",
        lambda _path, *args: "dirty" if args == ("status", "--porcelain") else pins[0].commit_sha,
    )
    with pytest.raises(ceremony.CeremonyError):
        _run(checkouts=pins, route=route, ports=Driver().ports())


def test_wrong_sha_fails_before_dispatch(route, pins, monkeypatch):
    calls = []
    driver = Driver()
    ports = driver.ports()
    ports = ceremony.CeremonyPorts(**{**ports.__dict__, "dispatch": lambda *_args: calls.append(True)})
    monkeypatch.setattr(
        ceremony,
        "_git",
        lambda _path, *args: "d" * 40 if args == ("rev-parse", "HEAD") else "",
    )
    with pytest.raises(ceremony.CeremonyError, match="SHA mismatch"):
        _run(checkouts=pins, route=route, ports=ports)
    assert calls == []


def test_arbitrary_checkout_label_cannot_satisfy_component_gate(tmp_path):
    with pytest.raises(ValueError, match="canonical component"):
        ceremony.ReviewedCheckout("arbitrary", tmp_path, "a" * 40)


def test_missing_reviewed_module_fails_before_dispatch(route, pins):
    calls = []
    pins[0].worktree.joinpath("sc_assignment_runtime.py").unlink()
    ports = ceremony.CeremonyPorts(**{**Driver().ports().__dict__, "dispatch": lambda *_args: calls.append(True)})
    with pytest.raises(ceremony.CeremonyError, match="reviewed module is missing"):
        _run(checkouts=pins, route=route, ports=ports)
    assert calls == []


@pytest.mark.parametrize(
    "field,value",
    (("hwnd", 0), ("pid", True), ("process_start_time_ns", -1), ("tab_runtime_id", ())),
)
def test_exact_route_is_mandatory(field, value, route, pins):
    values = dict(route.__dict__)
    values[field] = value
    with pytest.raises(ValueError):
        ceremony.ExactRoute(**values)


def test_guarded_submit_shape_cannot_be_weakened(route, pins):
    driver = Driver()
    original = driver.dispatch

    for missing in ("delivery_verified", "peer_acknowledged", "decision", "state"):

        def dispatch(case, trigger, missing=missing):
            result = original(case, trigger)
            result["submit_result"].pop(missing)
            return result

        ports = ceremony.CeremonyPorts(**{**driver.ports().__dict__, "dispatch": dispatch})
        with pytest.raises(ceremony.CeremonyError, match="authenticate"):
            _run(checkouts=pins, route=route, ports=ports)


def test_wrong_seat_assignment_fails(route, pins):
    driver = Driver()
    original = driver.dispatch

    def dispatch(case, trigger):
        result = original(case, trigger)
        result["assignment"]["receiver_birth_id"] = "attacker-seat"
        return result

    ports = ceremony.CeremonyPorts(**{**driver.ports().__dict__, "dispatch": dispatch})
    with pytest.raises(ceremony.CeremonyError, match="exact intended seat"):
        _run(checkouts=pins, route=route, ports=ports)


def test_sequence_gap_and_wrong_ack_fail(route, pins):
    driver = Driver()
    original_transition = driver.transition

    def gap(assignment, state):
        receipt = original_transition(assignment, state)
        if state == "working":
            receipt["sequence"] += 1
        return receipt

    ports = ceremony.CeremonyPorts(**{**driver.ports().__dict__, "seat_transition": gap})
    with pytest.raises(ceremony.CeremonyError, match="state/sequence"):
        _run(checkouts=pins, route=route, ports=ports)

    driver = Driver()
    ports = ceremony.CeremonyPorts(
        **{
            **driver.ports().__dict__,
            "acknowledge_receipt": lambda receipt: {
                "assignment_id": receipt["assignment_id"],
                "sequence": receipt["sequence"],
                "receipt_sha256": "0" * 64,
            },
        }
    )
    with pytest.raises(ceremony.CeremonyError, match="exact verified receipt"):
        _run(checkouts=pins, route=route, ports=ports)


@pytest.mark.parametrize(
    "field",
    ("birth_id", "generation", "seat_key_id", "seat_epoch"),
)
def test_failover_requires_fresh_birth_generation_key_and_epoch(field, route, pins):
    driver = Driver()
    original = driver.failover

    def failover(assignment, receipt):
        result = original(assignment, receipt)
        old = result["old_seat"]
        replacement = result["replacement_seat"]
        replacement[field] = old[field]
        return result

    ports = ceremony.CeremonyPorts(**{**driver.ports().__dict__, "failover": failover})
    with pytest.raises((ceremony.CeremonyError, ValueError), match=r"fresh|generation"):
        _run(checkouts=pins, route=route, ports=ports)


def test_process_kill_and_missing_delivery_receipt_reject(route, pins):
    driver = Driver()
    original = driver.failover
    for mutation in (
        lambda result: result.update(process_action="terminate"),
        lambda result: result.pop("delivery_receipt"),
    ):

        def failover(assignment, receipt, mutation=mutation):
            result = original(assignment, receipt)
            mutation(result)
            return result

        ports = ceremony.CeremonyPorts(**{**driver.ports().__dict__, "failover": failover})
        with pytest.raises(ceremony.CeremonyError):
            _run(checkouts=pins, route=route, ports=ports)


def test_every_negative_probe_must_raise(route, pins):
    for allowed in ceremony._NEGATIVE_PROBES:
        driver = Driver()

        def negative(probe, allowed=allowed):
            if probe == allowed:
                return None
            raise ProtocolRejected("rejected")

        ports = ceremony.CeremonyPorts(**{**driver.ports().__dict__, "negative_probe": negative})
        with pytest.raises(ceremony.CeremonyError, match=allowed):
            _run(checkouts=pins, route=route, ports=ports)


@pytest.mark.parametrize("error", (NotImplementedError, AttributeError, RuntimeError, ValueError))
def test_unrelated_negative_probe_errors_are_harness_failures(error, route, pins):
    def negative(_probe):
        raise error("not protocol verification")

    ports = ceremony.CeremonyPorts(**{**Driver().ports().__dict__, "negative_probe": negative})
    with pytest.raises(ceremony.CeremonyError, match=f"unrelated error: wrong_seat:{error.__name__}"):
        _run(checkouts=pins, route=route, ports=ports)


@pytest.mark.parametrize("error", (Exception, RuntimeError, NotImplementedError, AttributeError))
def test_broad_or_harness_errors_cannot_be_declared_protocol_errors(error):
    values = Driver().ports().__dict__
    with pytest.raises(TypeError, match="specific protocol verification"):
        ceremony.CeremonyPorts(**{**values, "protocol_errors": (error,)})


def test_final_bundle_is_signed_timestamped_operator_bound_and_pins_exact_shas(route, pins):
    bundle = _run(checkouts=pins, route=route, ports=Driver().ports())
    body = ceremony.verify_evidence_bundle(bundle, trust=_TRUST)
    assert body["coordinator_id"] == _TRUST.coordinator_id
    assert body["key_id"] == _TRUST.key_id
    assert body["operator_id"] == _TRUST.operator_id
    assert body["operator_authorization_id"] == _TRUST.operator_authorization_id
    assert body["pinned_shas"] == {item.component: item.commit_sha for item in pins}
    issued = datetime.strptime(body["issued_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    expires = datetime.strptime(body["expires_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert expires - issued == timedelta(seconds=60)
    assert len(body["evidence_nonce"]) == 64
    assert bundle["signature"]["algorithm"] == "Ed25519"


@pytest.mark.parametrize("field", ("operator_id", "operator_authorization_id", "coordinator_id", "key_id"))
def test_signed_bundle_identity_tampering_rejects(field, route, pins):
    bundle = _run(checkouts=pins, route=route, ports=Driver().ports())
    forged = deepcopy(bundle)
    forged["body"][field] = "attacker"
    with pytest.raises(ceremony.CeremonyError, match="signer/operator binding"):
        ceremony.verify_evidence_bundle(forged, trust=_TRUST)


def test_signed_bundle_pin_tampering_and_expiry_reject(route, pins):
    bundle = _run(checkouts=pins, route=route, ports=Driver().ports())
    forged = deepcopy(bundle)
    forged["body"]["pinned_shas"]["runtime"] = "f" * 40
    with pytest.raises(ceremony.CeremonyError, match="pinned SHA binding"):
        ceremony.verify_evidence_bundle(forged, trust=_TRUST)
    expires = datetime.strptime(bundle["body"]["expires_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    with pytest.raises(ceremony.CeremonyError, match="outside its validity window"):
        ceremony.verify_evidence_bundle(bundle, trust=_TRUST, now=expires + timedelta(seconds=6))


def test_rehashed_body_tampering_still_fails_coordinator_signature(route, pins):
    bundle = _run(checkouts=pins, route=route, ports=Driver().ports())
    forged = deepcopy(bundle)
    forged["body"]["cases"][0]["trigger"] = "completed"
    forged["body_sha256"] = _digest(forged["body"])
    with pytest.raises(ceremony.CeremonyError, match="signature is invalid"):
        ceremony.verify_evidence_bundle(forged, trust=_TRUST)


def test_wrong_coordinator_key_and_signer_failure_reject(route, pins):
    attacker = Ed25519PrivateKey.generate()
    wrong_signer = ceremony.EvidenceSigner(trust=_TRUST, sign=attacker.sign)
    with pytest.raises(ceremony.CeremonyError, match="signature is invalid"):
        _run(checkouts=pins, route=route, ports=Driver().ports(), evidence_signer=wrong_signer)

    def broken_signer(_payload):
        raise OSError("key unavailable")

    unavailable = ceremony.EvidenceSigner(trust=_TRUST, sign=broken_signer)
    with pytest.raises(ceremony.CeremonyError, match="signing failed"):
        _run(checkouts=pins, route=route, ports=Driver().ports(), evidence_signer=unavailable)


def test_evidence_trust_requires_exact_operator_and_coordinator_identity():
    values = dict(_TRUST.__dict__)
    for field in ("coordinator_id", "key_id", "operator_id", "operator_authorization_id"):
        with pytest.raises(ValueError, match=field):
            ceremony.EvidenceTrust(**{**values, field: ""})


def test_authority_surface_has_no_screen_or_postmessage_inputs():
    import inspect

    parameters = set(inspect.signature(ceremony.run_live_ceremony).parameters)
    assert parameters == {"checkouts", "route", "ports", "evidence_signer"}
    names = {str(name).lower() for name in ceremony.run_live_ceremony.__code__.co_names}
    assert all(term not in names for term in ("uia", "ocr", "composer", "postmessage"))
    assert len(ceremony.missing_live_prerequisites()) == 8
