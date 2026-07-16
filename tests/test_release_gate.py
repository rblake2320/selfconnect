from __future__ import annotations

import json
from pathlib import Path

import tools.release_gate as release_gate
from tools.release_gate import (
    _audit_claims,
    _benchmark_summary,
    _readme_claim_blocks,
    _sha256_normalized_text,
    audit,
    compare,
)

ROOT = Path(__file__).resolve().parents[1]


def test_repository_release_metadata_and_claim_evidence_pass() -> None:
    report = audit(ROOT, allow_dirty=True)
    failures = [check for check in report["checks"] if check["status"] == "fail"]
    assert failures == []
    assert report["claims"]["release_ledger_coverage_percent"] == 100.0
    assert "not README claim coverage" in report["claims"][
        "release_ledger_coverage_scope"
    ]
    assert report["claims"]["tagged_readme_valid"] == 24
    assert report["claims"]["tagged_readme_total"] == 24
    assert report["claims"]["tagged_readme_coverage_percent"] == 100.0
    assert report["claims"]["natural_language_claim_detection"].startswith("PARTIAL:")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Why This Is Novel" not in readme
    assert "All proved live" not in readme


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
    assert summary["release_ledger_valid"] == 0
    assert checks[0].status == "fail"
    assert "hash mismatch" in checks[0].detail


def test_claim_binary_evidence_uses_raw_file_sha256(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.bin"
    evidence.write_bytes(b"binary\r\npayload\x00")
    claims = {
        "policy": {"release_statuses": ["proven"], "non_release_statuses": []},
        "claims": [
            {
                "id": "binary.evidence",
                "statement": "Scoped binary evidence.",
                "status": "proven",
                "release": True,
                "scope": "test",
                "boundary": "test only",
                "verified_on": "2026-07-15",
                "evidence": [
                    {
                        "path": "evidence.bin",
                        "sha256": release_gate._sha256(evidence),
                    }
                ],
            }
        ],
    }
    checks = []
    summary = _audit_claims(tmp_path, claims, checks)
    assert summary["release_ledger_valid"] == 1
    assert _check(checks, "claim.binary.evidence").status == "pass"


def _readme_block(claim_id: str, content: str = "Bounded claim.\n") -> str:
    return (
        f"<!-- SC-CLAIM:{claim_id} START -->\n"
        f"{content}"
        f"<!-- SC-CLAIM:{claim_id} END -->\n"
    )


def _tagged_claim_document(
    tmp_path: Path,
    claim_id: str,
    content: str = "Bounded claim.\n",
) -> dict:
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("named evidence\n", encoding="utf-8")
    return {
        "policy": {"release_statuses": ["proven"], "non_release_statuses": []},
        "claims": [
            {
                "id": claim_id,
                "statement": "Bounded claim.",
                "status": "proven",
                "release": True,
                "scope": "test",
                "boundary": "test only",
                "verified_on": "2026-07-15",
                "public_readme": {
                    "tag": claim_id,
                    "path": "README.md",
                    "sha256_text": _sha256_normalized_text(content),
                },
                "evidence": [{"path": "evidence.txt"}],
            }
        ],
    }


def _check(checks: list, check_id: str):
    return next(check for check in checks if check.check_id == check_id)


def test_unregistered_tagged_readme_claim_fails(tmp_path: Path) -> None:
    checks = []
    summary = _audit_claims(
        tmp_path,
        {"policy": {"release_statuses": [], "non_release_statuses": []}, "claims": []},
        checks,
        readme_text=_readme_block("public.unregistered"),
    )
    assert summary["tagged_readme_total"] == 1
    assert summary["tagged_readme_valid"] == 0
    result = _check(checks, "truth.readme_tagged_claims")
    assert result.status == "fail"
    assert "unregistered README claim tag" in result.detail


def test_duplicate_tagged_readme_claim_fails(tmp_path: Path) -> None:
    claim_id = "public.duplicate"
    checks = []
    _audit_claims(
        tmp_path,
        _tagged_claim_document(tmp_path, claim_id),
        checks,
        readme_text=_readme_block(claim_id) + _readme_block(claim_id),
    )
    result = _check(checks, "truth.readme_tagged_claims")
    assert result.status == "fail"
    assert "duplicate claim tag" in result.detail


def test_mismatched_public_readme_mapping_fails(tmp_path: Path) -> None:
    claim_id = "public.mapped"
    claims = _tagged_claim_document(tmp_path, claim_id)
    claims["claims"][0]["public_readme"]["tag"] = "public.other"
    checks = []
    summary = _audit_claims(
        tmp_path,
        claims,
        checks,
        readme_text=_readme_block(claim_id),
    )
    assert summary["tagged_readme_valid"] == 0
    result = _check(checks, f"claim.{claim_id}")
    assert result.status == "fail"
    assert "public_readme tag mismatch" in result.detail


def test_mismatched_public_readme_excerpt_hash_fails(tmp_path: Path) -> None:
    claim_id = "public.hash"
    claims = _tagged_claim_document(tmp_path, claim_id)
    claims["claims"][0]["public_readme"]["sha256_text"] = "0" * 64
    checks = []
    summary = _audit_claims(
        tmp_path,
        claims,
        checks,
        readme_text=_readme_block(claim_id),
    )
    assert summary["tagged_readme_valid"] == 0
    result = _check(checks, f"claim.{claim_id}")
    assert result.status == "fail"
    assert "public_readme excerpt hash mismatch" in result.detail


def test_malformed_tagged_readme_claim_fails(tmp_path: Path) -> None:
    checks = []
    _audit_claims(
        tmp_path,
        {"policy": {"release_statuses": [], "non_release_statuses": []}, "claims": []},
        checks,
        readme_text="<!-- SC-CLAIM:public.bad START-- >\nBounded claim.\n",
    )
    result = _check(checks, "truth.readme_tagged_claims")
    assert result.status == "fail"
    assert "malformed claim tag" in result.detail


def test_claim_block_parser_rejects_mismatched_end_tag() -> None:
    blocks, errors = _readme_claim_blocks(
        "<!-- SC-CLAIM:public.one START -->\n"
        "Bounded claim.\n"
        "<!-- SC-CLAIM:public.two END -->\n"
    )
    assert blocks == {}
    assert any("does not match" in error for error in errors)


def test_compare_reports_objective_improvement_without_quality_score() -> None:
    baseline = {
        "repo": {"commit": "a"},
        "counts": {"fail": 3},
        "claims": {
            "release_ledger_coverage_percent": 80.0,
            "tagged_readme_coverage_percent": 75.0,
        },
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
        "claims": {
            "release_ledger_coverage_percent": 100.0,
            "tagged_readme_coverage_percent": 100.0,
        },
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
    assert result["release_gate"]["candidate_release_ledger_coverage_percent"] == 100.0
    assert result["release_gate"]["candidate_tagged_readme_coverage_percent"] == 100.0
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
