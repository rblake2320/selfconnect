from __future__ import annotations

import json
from pathlib import Path

from tools.candidate_coherence import REQUIRED_MODULES, validate_candidate


def _candidate(tmp_path: Path, *, complete: bool) -> Path:
    root = tmp_path / "candidate"
    (root / ".github/workflows").mkdir(parents=True)
    (root / "release").mkdir()
    for name in REQUIRED_MODULES if complete else REQUIRED_MODULES[:2]:
        (root / name).write_text("# candidate\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[tool.hatch.build.targets.wheel]\ninclude = [\n" +
        "\n".join(f'  "{name}",' for name in REQUIRED_MODULES if complete) +
        "\n]\n", encoding="utf-8")
    (root / "release/core_invariants.json").write_text(
        json.dumps({"required_wheel_modules": list(REQUIRED_MODULES if complete else REQUIRED_MODULES[:1])}),
        encoding="utf-8",
    )
    (root / ".github/workflows/ci.yml").write_text(
        "steps:\n  - run: python tools/candidate_coherence.py --root .\n", encoding="utf-8"
    )
    (root / "release/claims.json").write_text(json.dumps({"claims": []}), encoding="utf-8")
    return root


def test_base_or_partial_candidate_reports_implementation_failures(tmp_path: Path):
    report = validate_candidate(_candidate(tmp_path, complete=False))
    assert report["ok"] is False
    findings = {item["code"]: item for item in report["findings"]}
    assert findings["implementation.modules"]["status"] == "fail"
    assert "sc_assignment_watchdog.py" in findings["implementation.modules"]["detail"]
    assert findings["release.invariants"]["status"] == "fail"


def test_complete_candidate_passes_with_explicit_root(tmp_path: Path):
    root = _candidate(tmp_path, complete=True)
    report = validate_candidate(root, policy_root=root)
    assert report["ok"] is True
    assert all(item["status"] in {"pass", "expected_premerge"} for item in report["findings"])


def test_workflow_hook_and_manifest_are_independent_gates(tmp_path: Path):
    root = _candidate(tmp_path, complete=True)
    (root / ".github/workflows/ci.yml").write_text("steps: []\n", encoding="utf-8")
    report = validate_candidate(root)
    assert report["ok"] is False
    findings = {item["code"]: item for item in report["findings"]}
    assert findings["ci.workflow"]["status"] == "fail"


def test_claim_hash_drift_is_separate_from_implementation_failure(tmp_path: Path):
    root = _candidate(tmp_path, complete=True)
    (root / "release/claims.json").write_text(json.dumps({"claims": [{
        "id": "x", "evidence": [{"path": "sc_assignment_protocol.py", "sha256_text": "0" * 64}]
    }]}), encoding="utf-8")
    report = validate_candidate(root)
    assert report["ok"] is True
    findings = {item["code"]: item for item in report["findings"]}
    assert findings["claim_hash_drift"]["status"] == "expected_premerge"
