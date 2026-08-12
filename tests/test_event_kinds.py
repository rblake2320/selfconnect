from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import sc_event_kinds
import sc_fabric_benchmark
import sc_fleet_guard
import sc_mesh_registry


def _agent(role: str, *, hwnd: int = 0, pid: int = 0, created_at: float | None = None):
    virtual = hwnd == 0
    title = role
    return {
        "mesh": "default", "role": role, "agent": "local_model" if virtual else "codex",
        "label": role, "birth_id": f"{role}-birth", "generation": 1,
        "created_at": created_at or time.time(), "last_seen": time.time(),
        "hwnd": hwnd, "pid": pid, "exe_name": "" if virtual else "terminal.exe",
        "class_name": "virtual" if virtual else "ConsoleWindowClass", "title": title,
        "window_fingerprint": sc_mesh_registry._window_fingerprint(
            hwnd=hwnd, pid=pid, class_name="virtual" if virtual else "ConsoleWindowClass", title=title
        ),
        "task": "", "status": "active", "profile": "explore", "notes": "",
        "session_id": None, "is_terminal": not virtual,
    }


def test_v2_event_has_numeric_kind_and_legacy_chain_remains_valid(tmp_path):
    path = tmp_path / "events.jsonl"
    legacy = {
        "version": 1,
        "event_id": "legacy",
        "event_type": "role_heartbeat",
        "created_at": 1.0,
        "prev_event_hash": sc_mesh_registry.EVENT_GENESIS_HASH,
    }
    legacy["event_hash"] = sc_mesh_registry.compute_event_hash(legacy)
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    appended = sc_mesh_registry.append_event(
        "role_heartbeat", event_log_path=path, repo_snapshot={}
    )["event"]
    assert appended["version"] == 2
    assert appended["kind"] == sc_event_kinds.BY_TYPE["role_heartbeat"].kind
    assert appended["kind_class"] == sc_event_kinds.OBSERVATIONAL
    assert sc_mesh_registry.verify_events(event_log_path=path)["ok"] is True


def test_unknown_observation_is_ignored_but_protected_kind_fails_closed():
    observation = {
        "version": 2,
        "event_type": "future.telemetry",
        "kind": 7_777,
        "kind_version": 1,
        "kind_class": "observational",
    }
    assert sc_event_kinds.dispatch_event(observation, {})["ignored"] is True
    protected = {**observation, "kind": 150, "kind_class": "observational"}
    with pytest.raises(sc_event_kinds.UnknownProtectedEventKind):
        sc_event_kinds.dispatch_event(protected, {})


def test_event_log_rejects_v1_downgrade_after_v2(tmp_path):
    path = tmp_path / "events.jsonl"
    current = sc_mesh_registry.append_event(
        "role_heartbeat", event_log_path=path, repo_snapshot={}
    )["event"]
    legacy = {
        "version": 1,
        "event_id": "downgrade",
        "event_type": "role_heartbeat",
        "created_at": 2.0,
        "prev_event_hash": current["event_hash"],
    }
    legacy["event_hash"] = sc_mesh_registry.compute_event_hash(legacy)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(legacy) + "\n")
    verified = sc_mesh_registry.verify_events(event_log_path=path)
    assert verified["ok"] is False
    assert any(item["error"] == "event_schema_downgrade" for item in verified["errors"])


@pytest.mark.parametrize("bad_version", [True, 1.9, "2"])
def test_event_log_rejects_coercive_schema_versions(tmp_path, bad_version):
    path = tmp_path / "events.jsonl"
    item = {
        "version": bad_version, "event_id": "bad-version", "event_type": "role_heartbeat",
        "created_at": 1.0, "prev_event_hash": sc_mesh_registry.EVENT_GENESIS_HASH,
    }
    item["event_hash"] = sc_mesh_registry.compute_event_hash(item)
    path.write_text(json.dumps(item) + "\n", encoding="utf-8")
    verified = sc_mesh_registry.verify_events(event_log_path=path)
    assert verified["ok"] is False
    assert any(error["error"] == "invalid_event_version" for error in verified["errors"])


@pytest.mark.parametrize(("field", "value"), [("kind", 1000.5), ("kind", True), ("kind_version", "1")])
def test_v2_event_rejects_coercive_kind_fields(field, value):
    record = {
        "version": 2, "event_type": "role_heartbeat", "kind": 1001,
        "kind_version": 1, "kind_class": "observational",
    }
    record[field] = value
    assert sc_event_kinds.validate_envelope(record) == "invalid_kind_envelope"


def test_live_fleet_snapshot_and_guard_need_no_state_file(tmp_path, monkeypatch):
    registry_path = tmp_path / "mesh_registry.json"
    event_path = tmp_path / "mesh_events.jsonl"
    sc_mesh_registry.save_registry({
        "version": 1,
        "agents": [_agent("agent-a")],
    }, registry_path)
    sc_mesh_registry.append_event(
        "role_heartbeat", registry_path=registry_path, event_log_path=event_path, repo_snapshot={}
    )
    monkeypatch.setattr(sc_fleet_guard, "resource_snapshot", lambda: {"ram_free_mb": 100_000, "gpu": None})
    state = sc_fleet_guard.live_state_snapshot(
        registry_path=registry_path, event_log_path=event_path
    )
    assert state["event_log_ok"] is True
    assert state["agents"][0]["name"] == "agent-a"


def test_registry_working_observation_is_not_authenticated_processing_proof(tmp_path, monkeypatch):
    registry_path = tmp_path / "mesh_registry.json"
    event_path = tmp_path / "mesh_events.jsonl"
    row = _agent("self-reported-worker", hwnd=10, pid=20)
    sc_mesh_registry.save_registry({"version": 1, "agents": [row]}, registry_path)
    updated = sc_mesh_registry.update_agent(
        "self-reported-worker", status="working", registry_path=registry_path
    )
    assert updated["ok"] is True
    assert sc_mesh_registry.verify_events(event_log_path=event_path)["ok"] is True

    monkeypatch.setattr(
        sc_fleet_guard,
        "resource_snapshot",
        lambda: {"ram_free_mb": 1, "gpu": None},
    )
    state = sc_fleet_guard.live_state_snapshot(
        registry_path=registry_path,
        event_log_path=event_path,
    )

    assert state["agents"][0]["status"] == "working"
    assert state["agents"][0]["status_source"] == "registry_observation"
    assert state["agents"][0]["processing_proven"] is False
    assert state["processing_proven"] is False
    assert state["run_active"] is False
    assert state["operational_activity_observed"] is True
    assert state["resource_guard_active"] is True

    verdict = sc_fleet_guard.evaluate_fleet(
        state["agents"],
        resources=state["resources"],
        thresholds=state["thresholds"],
        run_active=state["resource_guard_active"],
        event_log_ok=state["event_log_ok"],
    )
    assert verdict["verdict"] == "halt_recommended"
    assert any(reason["kind"] == "ram_floor" for reason in verdict["halt_reasons"])


def test_live_fleet_snapshot_fails_closed_on_corrupt_registry(tmp_path, monkeypatch):
    registry_path = tmp_path / "mesh_registry.json"
    registry_path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(sc_fleet_guard, "resource_snapshot", lambda: {"ram_free_mb": 100_000})
    state = sc_fleet_guard.live_state_snapshot(registry_path=registry_path)
    verdict = sc_fleet_guard.evaluate_fleet(
        state["agents"], resources=state["resources"], event_log_ok=state["event_log_ok"]
    )
    assert state["registry_ok"] is False
    assert verdict["verdict"] == "hard_stop"


def test_live_fleet_snapshot_fails_closed_on_malformed_agent_entry(tmp_path, monkeypatch):
    registry_path = tmp_path / "mesh_registry.json"
    registry_path.write_text(json.dumps({"version": 1, "agents": [None]}), encoding="utf-8")
    monkeypatch.setattr(sc_fleet_guard, "resource_snapshot", lambda: {"ram_free_mb": 100_000})

    state = sc_fleet_guard.live_state_snapshot(registry_path=registry_path)
    verdict = sc_fleet_guard.evaluate_fleet(
        state["agents"], resources=state["resources"], event_log_ok=state["event_log_ok"]
    )

    assert state["registry_ok"] is False
    assert verdict["verdict"] == "hard_stop"


def test_live_fleet_snapshot_fails_closed_on_unknown_agent_status(tmp_path, monkeypatch):
    registry_path = tmp_path / "mesh_registry.json"
    row = _agent("typo-status")
    row["status"] = "actve"
    registry_path.write_text(json.dumps({"version": 1, "agents": [row]}), encoding="utf-8")
    monkeypatch.setattr(sc_fleet_guard, "resource_snapshot", lambda: {"ram_free_mb": 100_000})

    state = sc_fleet_guard.live_state_snapshot(registry_path=registry_path)

    assert state["registry_ok"] is False
    assert state["event_log_ok"] is False


def test_stale_danger_status_does_not_age_out_of_fleet_guard(tmp_path, monkeypatch):
    registry_path = tmp_path / "mesh_registry.json"
    row = _agent("invalidated-live", hwnd=10, pid=20)
    row["status"] = "invalidated"
    row["guard_ok"] = False
    row["last_seen"] = time.time() - 2_000
    sc_mesh_registry.save_registry({"version": 1, "agents": [row]}, registry_path)
    monkeypatch.setattr(sc_fleet_guard, "resource_snapshot", lambda: {"ram_free_mb": 100_000})

    state = sc_fleet_guard.live_state_snapshot(registry_path=registry_path)
    verdict = sc_fleet_guard.evaluate_fleet(
        state["agents"], resources=state["resources"], event_log_ok=state["event_log_ok"]
    )

    assert state["agents"][0]["status"] == "invalidated"
    assert verdict["verdict"] == "hard_stop"


def test_freeze_root_resolves_from_module_when_cwd_is_unrelated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    status = sc_fabric_benchmark.patent_freeze_status()
    assert Path(status["repo_root"]) == Path(sc_fabric_benchmark.__file__).resolve().parent
    assert status["ok"] is True


def test_reconcile_invalidates_stale_and_standbys_later_alias(tmp_path, monkeypatch):
    registry_path = tmp_path / "mesh_registry.json"
    agents = [
        _agent("canonical", hwnd=10, pid=1, created_at=1.0),
        _agent("alias", hwnd=10, pid=1, created_at=2.0),
        _agent("stale", hwnd=11, pid=2, created_at=3.0),
    ]
    for item in agents:
        item["title"] = "shared" if item["hwnd"] == 10 else "stale"
        item["window_fingerprint"] = sc_mesh_registry._window_fingerprint(
            hwnd=item["hwnd"], pid=item["pid"], class_name=item["class_name"], title=item["title"]
        )
    sc_mesh_registry.save_registry({"version": 1, "agents": agents}, registry_path)
    monkeypatch.setattr(
        sc_mesh_registry.sc_cli,
        "verify_target",
        lambda hwnd, **_kwargs: {
            "ok": hwnd == 10, "reasons": [] if hwnd == 10 else ["gone"],
            "actual": {
                "pid": 1 if hwnd == 10 else 0,
                "class_name": "ConsoleWindowClass" if hwnd == 10 else "",
                "title": "shared" if hwnd == 10 else "",
            },
        },
    )
    planned = sc_mesh_registry.reconcile_registry(registry_path=registry_path)
    assert planned["applied"] is False and planned["action_count"] == 2
    applied = sc_mesh_registry.reconcile_registry(registry_path=registry_path, apply=True)
    assert applied["action_count"] == 2
    rows = {item["role"]: item for item in sc_mesh_registry.load_registry(registry_path)["agents"]}
    assert rows["canonical"]["status"] == "active"
    assert rows["alias"]["status"] == "standby_alias"
    assert rows["stale"]["status"] == "invalidated"


def test_reconcile_rejects_unverified_intent_log(tmp_path):
    registry_path = tmp_path / "mesh_registry.json"
    event_path = tmp_path / "mesh_events.jsonl"
    sc_mesh_registry.save_registry({"version": 1, "agents": [_agent("agent-a")]}, registry_path)
    event_path.write_text(json.dumps({"event_type": "role_reconciled"}) + "\n", encoding="utf-8")

    with pytest.raises(sc_mesh_registry.EventLogIntegrityError, match="failed verification"):
        sc_mesh_registry.reconcile_registry(registry_path=registry_path, apply=True)


def test_reconcile_recovers_verified_intent_after_append_before_save(tmp_path, monkeypatch):
    registry_path = tmp_path / "mesh_registry.json"
    event_path = tmp_path / "mesh_events.jsonl"
    row = _agent("recover-me")
    sc_mesh_registry.save_registry({"version": 1, "agents": [row]}, registry_path)
    registry = sc_mesh_registry.load_registry_strict(registry_path)
    revision = __import__("hashlib").sha256(
        json.dumps(registry, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    sc_mesh_registry.append_event(
        "role_reconciled",
        **sc_mesh_registry._agent_event_payload(row),
        data={
            "role": "recover-me", "hwnd": 0, "from_status": "active",
            "to_status": "invalidated", "reason": "target_binding_invalid",
            "guard_reasons": ["simulated crash intent"],
            "source_registry_revision": revision,
        },
        registry_path=registry_path,
        event_log_path=event_path,
        strict=True,
        strict_idempotency_key=f"reconcile:{revision}:{row['birth_id']}:active:invalidated",
        repo_snapshot={},
    )
    monkeypatch.setenv("SELFCONNECT_MESH_EVENT_LOG", str(event_path))

    result = sc_mesh_registry.reconcile_registry(registry_path=registry_path, apply=True)

    assert result["actions"][0]["recovered_from_intent"] is True
    assert sc_mesh_registry.load_registry_strict(registry_path)["agents"][0]["status"] == "invalidated"
