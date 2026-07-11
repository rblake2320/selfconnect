import json
import sys

import pytest
from sc_mesh_lease import (
    GOVERNED_PROFILE,
    UNKNOWN_SID,
    LeaseDecision,
    RoleLeaseTable,
    _get_process_owner_sid_win32,
    current_owner_sid,
    evaluate_lease_gate,
    hash_sid,
    hash_text,
)


def _issue(
    table: RoleLeaseTable,
    *,
    hwnd=100,
    owner_sid="S-1-5-21-test",
    now=10.0,
    birth_id="",
):
    return table.issue(
        mesh="default",
        role="agent-a",
        hwnd=hwnd,
        pid=1234,
        exe_name="WindowsTerminal.exe",
        class_name="CASCADIA_HOSTING_WINDOW_CLASS",
        title="agent-a",
        owner_sid=owner_sid,
        ttl_s=30,
        now=now,
        birth_id=birth_id,
    )


def test_hash_helpers_are_stable_and_nonempty():
    assert hash_text("abc") == hash_text("abc")
    assert hash_text("abc") != hash_text("abcd")
    assert hash_sid("S-1-5") == hash_sid("S-1-5")
    assert hash_sid("") == hash_sid("<unknown-sid>")


def test_issue_starts_at_generation_one():
    lease = _issue(RoleLeaseTable())
    assert lease.generation == 1
    assert lease.owner_sid_hash == hash_sid("S-1-5-21-test")
    assert lease.title_hash == hash_text("agent-a")


def test_migration_increments_generation():
    table = RoleLeaseTable()
    first = _issue(table, hwnd=100)
    second = _issue(table, hwnd=200, now=20.0)
    assert first.generation == 1
    assert second.generation == 2
    assert table.current("default", "agent-a") == second


def test_validate_current_tuple_allows_ui_fallback():
    table = RoleLeaseTable()
    lease = _issue(table)
    result = table.validate_ui_fallback(
        mesh="default",
        role="agent-a",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid="S-1-5-21-test",
        now=11.0,
    )
    assert result.ok is True
    assert result.decision == LeaseDecision.ALLOW


def test_validate_rejects_stale_generation_after_migration():
    table = RoleLeaseTable()
    first = _issue(table, hwnd=100, now=10.0)
    second = _issue(table, hwnd=200, now=20.0)
    result = table.validate_ui_fallback(
        mesh="default",
        role="agent-a",
        generation=first.generation,
        hwnd=second.hwnd,
        owner_sid="S-1-5-21-test",
        now=21.0,
    )
    assert result.ok is False
    assert "generation" in result.reason


def test_validate_rejects_stale_hwnd():
    table = RoleLeaseTable()
    first = _issue(table, hwnd=100, now=10.0)
    second = _issue(table, hwnd=200, now=20.0)
    result = table.validate_ui_fallback(
        mesh="default",
        role="agent-a",
        generation=second.generation,
        hwnd=first.hwnd,
        owner_sid="S-1-5-21-test",
        now=21.0,
    )
    assert result.ok is False
    assert "hwnd" in result.reason


def test_validate_rejects_owner_sid_mismatch():
    table = RoleLeaseTable()
    lease = _issue(table)
    result = table.validate_ui_fallback(
        mesh="default",
        role="agent-a",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid="S-1-5-21-other",
        now=11.0,
    )
    assert result.ok is False
    assert "sid" in result.reason


def test_validate_rejects_expired_lease():
    table = RoleLeaseTable()
    lease = _issue(table, now=10.0)
    result = table.validate_ui_fallback(
        mesh="default",
        role="agent-a",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid="S-1-5-21-test",
        now=41.0,
    )
    assert result.ok is False
    assert "expired" in result.reason


def test_renew_extends_current_lease():
    table = RoleLeaseTable()
    lease = _issue(table, now=10.0)
    result = table.renew(
        mesh="default",
        role="agent-a",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid="S-1-5-21-test",
        ttl_s=100,
        now=20.0,
    )
    assert result.ok is True
    assert result.lease is not None
    assert result.lease.expires_at == 120.0


def test_to_dict_shapes_are_json_ready():
    table = RoleLeaseTable()
    lease = _issue(table)
    result = table.validate_ui_fallback(
        mesh="default",
        role="agent-a",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid="S-1-5-21-test",
        now=11.0,
    )
    data = result.to_dict()
    assert data["ok"] is True
    assert data["decision"] == "allow"
    assert data["lease"]["generation"] == 1


def test_gate_explore_is_allow_noop_without_table():
    gate = evaluate_lease_gate()
    assert gate.ok is True
    assert gate.decision == LeaseDecision.ALLOW
    assert "explore" in gate.reason.lower()


def test_gate_governed_allows_matching_lease():
    table = RoleLeaseTable()
    lease = _issue(table)
    gate = evaluate_lease_gate(
        profile=GOVERNED_PROFILE,
        table=table,
        mesh="default",
        role="agent-a",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid="S-1-5-21-test",
        now=11.0,
    )
    assert gate.ok is True
    assert gate.decision == LeaseDecision.ALLOW


def test_gate_governed_denies_wrong_generation():
    table = RoleLeaseTable()
    lease = _issue(table)
    gate = evaluate_lease_gate(
        profile=GOVERNED_PROFILE,
        table=table,
        mesh="default",
        role="agent-a",
        generation=lease.generation + 5,
        hwnd=lease.hwnd,
        owner_sid="S-1-5-21-test",
        now=11.0,
    )
    assert gate.ok is False
    assert "generation" in gate.reason


def test_gate_governed_denies_wrong_hwnd():
    table = RoleLeaseTable()
    lease = _issue(table)
    gate = evaluate_lease_gate(
        profile=GOVERNED_PROFILE,
        table=table,
        mesh="default",
        role="agent-a",
        generation=lease.generation,
        hwnd=lease.hwnd + 1,
        owner_sid="S-1-5-21-test",
        now=11.0,
    )
    assert gate.ok is False
    assert "hwnd" in gate.reason


def test_gate_governed_denies_wrong_owner_sid():
    table = RoleLeaseTable()
    lease = _issue(table)
    gate = evaluate_lease_gate(
        profile=GOVERNED_PROFILE,
        table=table,
        mesh="default",
        role="agent-a",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid="S-1-5-21-other",
        now=11.0,
    )
    assert gate.ok is False
    assert "sid" in gate.reason


def test_gate_explicit_lease_fields_enforce_even_in_explore_profile():
    # role+generation supplied while profile left "explore" -> governed path.
    gate = evaluate_lease_gate(
        profile="explore",
        table=None,
        mesh="default",
        role="agent-a",
        generation=1,
        hwnd=100,
        owner_sid="S-1-5-21-test",
    )
    assert gate.ok is False
    assert "table" in gate.reason


def test_gate_governed_without_table_denies():
    gate = evaluate_lease_gate(
        profile=GOVERNED_PROFILE,
        table=None,
        mesh="default",
        role="agent-a",
        generation=1,
        hwnd=100,
        owner_sid="S-1-5-21-test",
    )
    assert gate.ok is False
    assert "requires a lease table" in gate.reason


def test_gate_governed_requires_role_and_generation():
    table = RoleLeaseTable()
    gate = evaluate_lease_gate(
        profile=GOVERNED_PROFILE,
        table=table,
        mesh="default",
        owner_sid="S-1-5-21-test",
    )
    assert gate.ok is False
    assert "role and generation" in gate.reason


def test_gate_never_leaks_raw_sid_but_includes_owner_sid_hash():
    table = RoleLeaseTable()
    lease = _issue(table)
    raw_sid = "S-1-5-21-test"
    gate = evaluate_lease_gate(
        profile=GOVERNED_PROFILE,
        table=table,
        mesh="default",
        role="agent-a",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid=raw_sid,
        now=11.0,
    )
    serialized = json.dumps(gate.to_dict())
    assert raw_sid not in serialized
    assert gate.to_dict()["lease"]["owner_sid_hash"] == hash_sid(raw_sid)


def test_current_owner_sid_injection_and_sentinel():
    assert current_owner_sid(injected="S-1-5-x") == "S-1-5-x"
    resolved = current_owner_sid(None)
    assert isinstance(resolved, str)
    assert resolved != ""


# --- birth_id gate tests (req #3: role+birth_id+generation+hwnd+owner_sid_hash) ---


def test_issue_stores_birth_id():
    table = RoleLeaseTable()
    lease = _issue(table, birth_id="b-abc12345")
    assert lease.birth_id == "b-abc12345"


def test_issue_birth_id_defaults_to_empty():
    table = RoleLeaseTable()
    lease = _issue(table)
    assert lease.birth_id == ""


def test_validate_allows_with_matching_birth_id():
    table = RoleLeaseTable()
    lease = _issue(table, birth_id="b-abc12345")
    result = table.validate_ui_fallback(
        mesh="default",
        role="agent-a",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid="S-1-5-21-test",
        birth_id="b-abc12345",
        now=11.0,
    )
    assert result.ok is True


def test_validate_rejects_wrong_birth_id():
    table = RoleLeaseTable()
    lease = _issue(table, birth_id="b-abc12345")
    result = table.validate_ui_fallback(
        mesh="default",
        role="agent-a",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid="S-1-5-21-test",
        birth_id="b-wrong",
        now=11.0,
    )
    assert result.ok is False
    assert "birth_id" in result.reason


def test_validate_skips_birth_id_check_when_not_provided():
    table = RoleLeaseTable()
    lease = _issue(table, birth_id="b-abc12345")
    result = table.validate_ui_fallback(
        mesh="default",
        role="agent-a",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid="S-1-5-21-test",
        now=11.0,
    )
    assert result.ok is True


def test_gate_governed_denies_wrong_birth_id():
    table = RoleLeaseTable()
    lease = _issue(table, birth_id="b-correct")
    gate = evaluate_lease_gate(
        profile=GOVERNED_PROFILE,
        table=table,
        mesh="default",
        role="agent-a",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid="S-1-5-21-test",
        birth_id="b-wrong",
        now=11.0,
    )
    assert gate.ok is False
    assert "birth_id" in gate.reason


def test_gate_governed_allows_matching_birth_id():
    table = RoleLeaseTable()
    lease = _issue(table, birth_id="b-correct")
    gate = evaluate_lease_gate(
        profile=GOVERNED_PROFILE,
        table=table,
        mesh="default",
        role="agent-a",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid="S-1-5-21-test",
        birth_id="b-correct",
        now=11.0,
    )
    assert gate.ok is True


def test_to_dict_includes_birth_id_in_lease():
    table = RoleLeaseTable()
    lease = _issue(table, birth_id="b-abc12345")
    result = table.validate_ui_fallback(
        mesh="default",
        role="agent-a",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid="S-1-5-21-test",
        birth_id="b-abc12345",
        now=11.0,
    )
    data = result.to_dict()
    assert data["lease"]["birth_id"] == "b-abc12345"


# --- Runtime OS SID lookup tests ---


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
def test_get_process_owner_sid_win32_returns_real_sid():
    sid = _get_process_owner_sid_win32()
    assert sid.startswith("S-1-"), f"expected real Windows SID, got {sid!r}"
    assert sid != UNKNOWN_SID


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
def test_current_owner_sid_no_injection_returns_real_sid_on_windows():
    sid = current_owner_sid()
    assert sid.startswith("S-1-"), f"expected real Windows SID, got {sid!r}"
    assert sid != UNKNOWN_SID


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
def test_governed_gate_allows_live_sid_and_denies_unknown():
    sid = current_owner_sid()
    table = RoleLeaseTable()
    lease = table.issue(
        mesh="default",
        role="B",
        hwnd=2820438,
        pid=1,
        exe_name="WindowsTerminal.exe",
        class_name="CASCADIA_HOSTING_WINDOW_CLASS",
        title="runtime-sid-test",
        owner_sid=sid,
        ttl_s=60,
        now=1.0,
    )

    allow = evaluate_lease_gate(
        profile=GOVERNED_PROFILE,
        table=table,
        mesh="default",
        role="B",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid=sid,
        now=2.0,
    )
    assert allow.ok is True, f"expected ALLOW with live SID, got {allow.reason}"

    deny = evaluate_lease_gate(
        profile=GOVERNED_PROFILE,
        table=table,
        mesh="default",
        role="B",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid=UNKNOWN_SID,
        now=3.0,
    )
    assert deny.ok is False, "expected DENY with UNKNOWN_SID (fail-closed)"
    assert "sid" in deny.reason


def test_unknown_sid_hash_equals_empty_string_hash():
    # Verifies fail-closed sentinel: empty SID maps to same hash as UNKNOWN_SID.
    assert hash_sid(UNKNOWN_SID) == hash_sid("")
