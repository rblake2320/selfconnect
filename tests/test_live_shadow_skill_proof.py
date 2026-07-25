"""Require the preserved independent M6 real-run artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROOFS = (
    ROOT / "proofs" / "capability_os" / "m6_live_shadow_skill_20260725.json",
    ROOT / "proofs" / "capability_os" / "m6_live_shadow_skill_repeat2_20260725.json",
    ROOT / "proofs" / "capability_os" / "m6_live_shadow_skill_repeat3_20260725.json",
)


@pytest.mark.parametrize("proof_path", PROOFS, ids=lambda path: path.stem)
def test_live_shadow_skill_proof(proof_path: Path) -> None:
    report = json.loads(proof_path.read_text(encoding="utf-8"))
    assert report["schema"] == "selfconnect.live-shadow-skill-proof.v1"
    assert report["ok"] is True
    assert report["evidence_verification"]["ok"] is True
    assert report["evidence_verification"]["records"] == 12
    assert report["candidate"]["source_run_count"] == 3
    assert report["candidate"]["status"] == "shadow"
    assert report["candidate"]["constraints"]["generated_code"] is False
    assert report["candidate"]["constraints"]["model_self_promotion"] is False
    assert report["candidate"]["constraints"]["separate_approval_required"] is True
    assert len(report["replays"]) == 3
    assert all(replay["ok"] is True for replay in report["replays"])
    assert report["adversarial"]["ok"] is True
    assert all(report["adversarial"]["cases"].values())
    assert report["eligibility"]["ok"] is True
    assert report["eligibility"]["still_shadow"] is True
    assert report["runtime_registry_contains_candidate"] is False
    assert report["approval_created"] is False
    assert len(report["real_io"]["source_contents"]) == 3
    assert len(report["real_io"]["replay_contents"]) == 3


def test_live_shadow_proofs_are_independent() -> None:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in PROOFS]
    assert len({report["run_id"] for report in reports}) == len(reports)
    assert len({
        report["candidate"]["candidate_id"] for report in reports
    }) == len(reports)
    assert len({
        report["evidence_verification"]["head_hash"] for report in reports
    }) == len(reports)
