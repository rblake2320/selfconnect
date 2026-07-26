"""Require the three independent governed M7 Qwen proofs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROOFS = (
    ROOT / "proofs" / "capability_os" / "m7_live_capability_os_rc_20260725.json",
    ROOT / "proofs" / "capability_os" / "m7_live_capability_os_rc_repeat2_20260725.json",
    ROOT / "proofs" / "capability_os" / "m7_live_capability_os_rc_repeat3_20260725.json",
)
EXPECTED_META_TOOLS = [
    "capability_discover",
    "capability_inspect",
    "capability_execute",
    "capability_task_create",
    "capability_task_continue",
]


@pytest.mark.parametrize("proof_path", PROOFS, ids=lambda path: path.stem)
def test_live_capability_os_rc_proof(proof_path: Path) -> None:
    report = json.loads(proof_path.read_text(encoding="utf-8"))
    assert report["schema"] == "selfconnect.live-capability-os-rc-proof.v1"
    assert report["ok"] is True
    assert report["model"] == "qwen3.6:27b"
    assert report["governance"]["ok"] is True
    assert report["governance"]["profile"] == "governed"
    assert report["governance"]["model_override_allowed"] is False
    assert report["meta_tools"] == EXPECTED_META_TOOLS
    assert report["called_tools"][:4] == [
        "capability_discover",
        "capability_inspect",
        "capability_task_create",
        "capability_task_continue",
    ]
    assert all(value in report["answer"] for value in report["expected_values"])
    assert report["evidence"]["ok"] is True
    assert report["evidence"]["records"] >= 8
    assert all(step["status"] == "completed" for step in report["task"]["steps"])
    assert report["shadow_compiler_integrated"] is True
    assert report["visual_specialist_integrated"] is True


def test_live_capability_os_rc_proofs_are_independent() -> None:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in PROOFS]
    assert len({report["run_id"] for report in reports}) == 3
    assert len({report["task"]["task_id"] for report in reports}) == 3
    assert len({report["evidence"]["head_hash"] for report in reports}) == 3
    assert len({tuple(report["expected_values"]) for report in reports}) == 3
