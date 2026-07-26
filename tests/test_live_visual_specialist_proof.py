"""Validate the preserved, repeated M5 live proof artifacts.

These tests do not substitute a fake model, fake GPU, or fake window.  They
make the release suite reject removal or weakening of the three independently
captured live runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROOFS = (
    ROOT / "proofs" / "capability_os" / "m5_live_visual_specialist_20260725.json",
    ROOT / "proofs" / "capability_os" / "m5_live_visual_specialist_repeat2_20260725.json",
    ROOT / "proofs" / "capability_os" / "m5_live_visual_specialist_repeat3_20260725.json",
)


@pytest.mark.parametrize("proof_path", PROOFS, ids=lambda path: path.stem)
def test_repeated_live_visual_specialist_proof(proof_path: Path) -> None:
    report = json.loads(proof_path.read_text(encoding="utf-8"))

    assert report["schema"] == "selfconnect.live-visual-specialist-proof.v1"
    assert report["ok"] is True
    assert len(report["run_id"]) == 32
    assert report["target"]["exe_name"].casefold() == "python.exe"
    assert report["target"]["title"].startswith("SelfConnect Visual Proof ")
    assert report["gpu"]["primary_delta_mb"] > 15_000

    before = report["before"]
    after = report["after"]
    assert before["guard"]["ok"] is True
    assert after["guard"]["ok"] is True
    assert before["admission"]["mode"] == "swap_primary_for_visual"
    assert after["admission"]["mode"] == "swap_primary_for_visual"
    assert before["admission"]["primary_was_loaded"] is True
    assert after["admission"]["primary_was_loaded"] is True
    assert before["visual"]["untrusted_data"] is True
    assert after["visual"]["untrusted_data"] is True
    assert before["state"] == "STATE: READY"
    assert after["state"] == "STATE: COMPLETE"
    assert before["uia"]["ok"] is True and before["ocr"]["ok"] is True
    assert after["uia"]["ok"] is True and after["ocr"]["ok"] is True
    assert before["restore"]["ok"] is True
    assert after["restore"]["ok"] is True

    action = report["action"]
    assert action["accepted"] is True
    assert action["raw_coordinates_used"] is False
    assert action["method"] == "semantic Win32 button text via BM_CLICK"
    assert report["models"]["primary"] in report["models"]["restored_loaded_models"]
    assert report["models"]["visual"] not in report["models"]["restored_loaded_models"]


def test_live_visual_runs_are_independent() -> None:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in PROOFS]

    assert len({report["run_id"] for report in reports}) == len(reports)
    assert len({report["role"] for report in reports}) == len(reports)
    assert len({report["hwnd"] for report in reports}) == len(reports)
    assert len({report["target"]["pid"] for report in reports}) == len(reports)
