import json
import tempfile
from pathlib import Path

import pytest
import sc_fabric_benchmark as bench
import sc_mesh_registry

_RESOURCES = {
    "ram_free_mb": 100_000,
    "gpu": {"vram_free_mb": 24_000},
}


def test_patent_freeze_status_is_ok_for_repo_docs():
    status = bench.patent_freeze_status()

    assert status["ok"] is True
    assert status["missing_docs"] == []
    assert status["missing_markers"] == []


def test_patent_freeze_status_fails_closed_without_docs():
    temp_dir = tempfile.TemporaryDirectory()

    try:
        status = bench.patent_freeze_status(temp_dir.name)
        assert status["ok"] is False
        assert "docs/PATENT_EVIDENCE_FREEZE_2026-06-20.md" in status["missing_docs"]
    finally:
        temp_dir.cleanup()


def test_v0_dry_run_writes_redacted_artifact_and_event_chain():
    temp_dir = tempfile.TemporaryDirectory()

    try:
        artifact = bench.run_benchmark(
            agent_count=2,
            messages_per_agent=1,
            stage="dry-run",
            profiles="normal",
            output_dir=temp_dir.name,
            run_id="test_dry_run",
            resources=_RESOURCES,
        )

        assert artifact["ok"] is True
        assert artifact["raw_text_included"] is False
        assert artifact["logical_message_count"] == 2
        assert artifact["aggregate"]["model_calls_per_known_task"] == 0
        assert Path(artifact["artifact_path"]).exists()
        assert sc_mesh_registry.verify_events(event_log_path=artifact["event_log_path"])["ok"] is True
    finally:
        temp_dir.cleanup()


def test_five_agent_production_run_writes_persisted_baseline():
    temp_dir = tempfile.TemporaryDirectory()

    try:
        artifact = bench.run_benchmark(
            agent_count=5,
            messages_per_agent=1,
            stage="production",
            profiles="normal",
            output_dir=temp_dir.name,
            run_id="test_baseline",
            resources=_RESOURCES,
        )

        baseline = artifact["baseline"]
        assert baseline["written"] is True
        baseline_path = Path(baseline["written_path"])
        assert baseline_path.name == "baseline_5agent.json"
        loaded = json.loads(baseline_path.read_text(encoding="utf-8"))
        assert loaded["agent_count"] == 5
        assert loaded["model_calls_per_known_task"] == 0
        assert loaded["transport_governance_p99_ms"] >= 0
    finally:
        temp_dir.cleanup()


def test_baseline_latency_regression_hard_stops():
    temp_dir = tempfile.TemporaryDirectory()

    try:
        baseline_path = Path(temp_dir.name) / "baseline_5agent.json"
        baseline_path.write_text(
            json.dumps({
                "schema_version": 1,
                "agent_count": 5,
                "transport": "current_transport",
                "transport_governance_p99_ms": 0.0001,
            }),
            encoding="utf-8",
        )

        artifact = bench.run_benchmark(
            agent_count=6,
            messages_per_agent=1,
            stage="dry-run",
            profiles="normal",
            output_dir=temp_dir.name,
            baseline_json=baseline_path,
            run_id="test_regression",
            resources=_RESOURCES,
        )

        assert artifact["verdict"] == "hard_stop"
        assert any(
            item["kind"] == "p99_latency_regression"
            for item in artifact["fleet_guard"]["hard_reasons"]
        )
    finally:
        temp_dir.cleanup()


def test_production_run_requires_freeze_unless_explicitly_overridden(monkeypatch):
    monkeypatch.setattr(bench, "patent_freeze_status", lambda *_: {"ok": False, "missing_docs": ["x"]})

    artifact = bench.run_benchmark(
        agent_count=5,
        messages_per_agent=1,
        stage="production",
        profiles="normal",
        output_dir=tempfile.gettempdir(),
        resources=_RESOURCES,
    )

    assert artifact["ok"] is False
    assert artifact["verdict"] == "freeze_required"


def test_unknown_profile_rejected():
    with pytest.raises(ValueError, match="unknown benchmark profile"):
        bench.run_benchmark(
            agent_count=1,
            profiles="unknown",
            resources=_RESOURCES,
        )
