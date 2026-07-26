"""Reject stale or incomplete real-hardware VRAM admission evidence."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _report(name: str) -> dict:
    return json.loads(
        (ROOT / "proofs" / "capability_os" / name).read_text(encoding="utf-8")
    )


def test_primary_vram_report_has_nine_cold_baseline_subtracted_trials() -> None:
    report = _report("m5_vram_cold_start_20260725.json")
    assert report["schema"] == "selfconnect.vram-cold-start.v1"
    assert report["ok"] is True
    assert report["model"] == "qwen3.6:27b"
    assert report["gpu_name"] == "NVIDIA GeForce RTX 5090"
    assert report["method"]["cold_start_each_trial"] is True
    assert report["method"]["baseline_subtracted"] is True
    assert len(report["trials"]) == 9
    assert {row["context"] for row in report["trials"]} == {8192, 16384, 32768}
    assert all(row["resident_models"] == ["qwen3.6:27b"] for row in report["trials"])
    assert all(row["delta_used_mb"] > 17_000 for row in report["trials"])
    assert report["summaries"]["32768"]["minimum_free_mb_after_load"] < 2_048


def test_visual_vram_report_bounds_admission_requirement() -> None:
    report = _report("m5_visual_vram_cold_start_20260725.json")
    assert report["ok"] is True
    assert report["model"] == "qwen3-vl:8b"
    assert len(report["trials"]) == 3
    measured_max = report["summaries"]["4096"]["delta_used_mb_max"]
    assert measured_max == 7_443
    assert measured_max < 7_800
