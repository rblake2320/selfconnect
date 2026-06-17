from sc_mesh_lease import LeaseDecision, RoleLeaseTable, hash_sid, hash_text


def _issue(table: RoleLeaseTable, *, hwnd=100, owner_sid="S-1-5-21-test", now=10.0):
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
