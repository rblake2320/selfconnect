from __future__ import annotations

import json
from pathlib import Path

import tools.release_gate as release_gate
from tools.release_gate import _audit_claims, _benchmark_summary, audit, compare

ROOT = Path(__file__).resolve().parents[1]


def test_repository_release_metadata_and_claim_evidence_pass() -> None:
    report = audit(ROOT, allow_dirty=True)
    failures = [check for check in report["checks"] if check["status"] == "fail"]
    assert failures == []
    assert report["claims"]["release_coverage_percent"] == 100.0


def test_policy_root_is_reported() -> None:
    report = audit(ROOT, policy_root=ROOT, allow_dirty=True)
    assert report["policy_root"] == str(ROOT)


def test_ruff_is_pinned_to_repository_config(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], root: Path, timeout: int = 300) -> dict:
        commands.append(command)
        return {
            "ok": True,
            "returncode": 0,
            "duration_seconds": 0.0,
            "output_tail": "All checks passed!",
        }

    monkeypatch.setattr(release_gate, "_run", fake_run)
    report = release_gate.audit(ROOT, allow_dirty=True, run_ruff=True)

    assert report["commands"]["ruff"]["ok"] is True
    ruff_command = commands[0]
    config_index = ruff_command.index("--config")
    assert Path(ruff_command[config_index + 1]) == ROOT / "pyproject.toml"


def test_recorded_service_benchmark_summary_is_exact() -> None:
    path = ROOT / "experiments/fabric_v2/results/SC_FABRIC_SERVICE_20260621_1135_redacted.json"
    summary = _benchmark_summary(path)
    assert summary["transport"] == "fabric_v2_service_transport"
    assert summary["agent_count"] == 5
    assert summary["metrics"]["transport_p99_ms"] == 1.049
    assert summary["metrics"]["end_to_end_p99_ms"] == 1.84
    assert summary["metrics"]["replay_attempts_accepted"] == 0


def test_claim_hash_mismatch_fails(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"ok": True}), encoding="utf-8")
    claims = {
        "policy": {"release_statuses": ["proven"], "non_release_statuses": ["pending"]},
        "claims": [
            {
                "id": "example",
                "statement": "Scoped example",
                "status": "proven",
                "release": True,
                "scope": "test",
                "boundary": "test only",
                "verified_on": "2026-07-09",
                "evidence": [{"path": "evidence.json", "sha256_text": "0" * 64}],
            }
        ],
    }
    checks = []
    summary = _audit_claims(tmp_path, claims, checks)
    assert summary["release_valid"] == 0
    assert checks[0].status == "fail"
    assert "hash mismatch" in checks[0].detail


def test_compare_reports_objective_improvement_without_quality_score() -> None:
    baseline = {
        "repo": {"commit": "a"},
        "counts": {"fail": 3},
        "claims": {"release_coverage_percent": 80.0},
        "benchmark": {
            "transport": "fabric",
            "agent_count": 5,
            "profile_names": ["normal"],
            "messages_per_agent": 3,
            "ok": True,
            "metrics": {
                "transport_p99_ms": 2.0,
                "replay_attempts_accepted": 0,
                "service_errors": 0,
            },
        },
    }
    candidate = {
        "repo": {"commit": "b"},
        "counts": {"fail": 1},
        "claims": {"release_coverage_percent": 100.0},
        "benchmark": {
            "transport": "fabric",
            "agent_count": 5,
            "profile_names": ["normal"],
            "messages_per_agent": 3,
            "ok": True,
            "metrics": {
                "transport_p99_ms": 1.0,
                "replay_attempts_accepted": 0,
                "service_errors": 0,
            },
        },
    }
    result = compare(baseline, candidate)
    assert result["release_gate"]["failure_delta"] == -2
    assert result["benchmark"]["comparable"] is True
    assert result["benchmark"]["correctness_regression"] is False
    assert result["benchmark"]["metric_deltas"]["transport_p99_ms"][
        "improvement_percent"
    ] == 50.0
    assert "quality_score" not in result


def test_compare_rejects_mismatched_workloads() -> None:
    baseline = {
        "counts": {"fail": 0},
        "claims": {},
        "benchmark": {
            "transport": "fabric",
            "agent_count": 5,
            "profile_names": ["normal"],
            "messages_per_agent": 3,
            "ok": True,
            "metrics": {},
        },
    }
    candidate = {
        "counts": {"fail": 0},
        "claims": {},
        "benchmark": {
            "transport": "fabric",
            "agent_count": 20,
            "profile_names": ["normal"],
            "messages_per_agent": 3,
            "ok": True,
            "metrics": {},
        },
    }
    result = compare(baseline, candidate)
    assert result["benchmark"]["comparable"] is False
    assert "agent_count" in result["benchmark"]["mismatches"]
