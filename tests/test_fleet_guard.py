import json
import tempfile
from pathlib import Path

import sc_fleet_guard as fleet
import sc_mesh_registry


def test_first_missed_ack_captures_without_hard_stop():
    result = fleet.evaluate_fleet([{"name": "codex-1", "missed_acks": 1}])

    assert result["verdict"] == "capture"
    assert result["action"] == "capture_and_continue"
    assert result["capture_triggers"][0]["kind"] == "missed_ack"
    assert result["agent_reports"][0]["risk"] == "yellow"


def test_three_blocked_agents_hard_stop():
    result = fleet.evaluate_fleet([
        {"name": "codex-1", "missed_acks": 2},
        {"name": "claude-1", "missed_acks": 2},
        {"name": "gemini-1", "missed_acks": 2},
    ])

    assert result["verdict"] == "hard_stop"
    assert result["blocked_count"] == 3
    assert any(item["kind"] == "blocked_agent_count" for item in result["hard_reasons"])


def test_wrong_sender_nonce_acceptance_hard_stops():
    result = fleet.evaluate_fleet([{"name": "agent-c", "wrong_sender_nonce_accepted": True}])

    assert result["verdict"] == "hard_stop"
    assert result["hard_reasons"] == [{"kind": "wrong_sender_nonce_accepted", "agent": "agent-c"}]


def test_ram_and_vram_floor_recommend_halt_not_process_kill():
    result = fleet.evaluate_fleet(
        [],
        resources={
            "ram_free_mb": 10_000,
            "gpu": {"vram_free_mb": 1_000},
        },
        local_models_active=True,
    )

    assert result["verdict"] == "halt_recommended"
    assert result["action"] == "stop_assigning_and_capture"
    assert {item["kind"] for item in result["halt_reasons"]} == {"ram_floor", "vram_floor"}


def test_vram_floor_ignored_when_local_models_inactive():
    result = fleet.evaluate_fleet(
        [],
        resources={
            "ram_free_mb": 40_000,
            "gpu": {"vram_free_mb": 1_000},
        },
        local_models_active=False,
    )

    assert result["verdict"] == "pass"


def test_latency_regression_hard_stops_against_baseline():
    result = fleet.evaluate_fleet(
        [{"name": "codex-1", "p99_latency_ms": 1200}],
        baseline={"p99_latency_ms": 200},
    )

    assert result["verdict"] == "hard_stop"
    reason = result["hard_reasons"][0]
    assert reason["kind"] == "p99_latency_regression"
    assert reason["limit_ms"] == 1000


def test_lifecycle_hooks_write_hash_chained_events():
    temp_dir = tempfile.TemporaryDirectory()
    event_path = Path(temp_dir.name) / "fleet_events.jsonl"

    try:
        fleet.fleet_register(
            name="codex-1",
            role="codex-1",
            birth_id="codex-1-a",
            generation=1,
            vendor="codex",
            task="stage 5",
            event_log_path=event_path,
        )
        fleet.fleet_heartbeat(
            name="codex-1",
            role="codex-1",
            birth_id="codex-1-a",
            generation=1,
            vendor="codex",
            ack_seq=3,
            latency_ms=42.5,
            event_log_path=event_path,
        )
        fleet.fleet_done(
            name="codex-1",
            role="codex-1",
            birth_id="codex-1-a",
            generation=1,
            vendor="codex",
            result="success",
            event_log_path=event_path,
        )

        loaded = sc_mesh_registry.load_events(event_log_path=event_path)
        assert [item["event_type"] for item in loaded["events"]] == [
            "fleet_agent_registered",
            "fleet_agent_heartbeat",
            "fleet_agent_done",
        ]
        assert loaded["events"][1]["data"]["ack_seq"] == 3
        assert sc_mesh_registry.verify_events(event_log_path=event_path)["ok"] is True
    finally:
        temp_dir.cleanup()


def test_guard_cli_reads_state_file(capsys):
    temp_dir = tempfile.TemporaryDirectory()
    state_path = Path(temp_dir.name) / "state.json"

    try:
        state_path.write_text(
            json.dumps({"agents": [{"name": "codex-1", "missed_acks": 1}]}),
            encoding="utf-8",
        )
        assert fleet.main(["guard", "--state-json", str(state_path)]) == 0
        output = json.loads(capsys.readouterr().out)
        assert output["verdict"] == "capture"
    finally:
        temp_dir.cleanup()
