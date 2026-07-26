from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROOF = ROOT / "proofs" / "capability_os" / "m3_live_qwen_recovery_20260725.json"


def test_committed_live_qwen_recovery_proof_is_self_consistent() -> None:
    proof = json.loads(PROOF.read_text(encoding="utf-8"))
    successor = proof["successor"]
    marker = ROOT / proof["predecessor"]["marker_path"]
    marker_hash = hashlib.sha256(marker.read_bytes()).hexdigest()

    assert proof["schema"] == "selfconnect.capability-os.m3-live-qwen-recovery.v1"
    assert proof["model"] == "qwen3.6:27b"
    assert proof["ok"] is True
    assert all(proof["checks"].values())
    assert proof["predecessor"]["instance_id"] != successor["instance_id"]
    assert proof["predecessor"]["qwen_response"] == "QWEN_PREDECESSOR_READY"
    assert successor["qwen_response"] == "QWEN_SUCCESSOR_READY"
    assert marker_hash == proof["predecessor"]["marker_sha256"]
    assert marker_hash == successor["marker_sha256_before"]
    assert marker_hash == successor["marker_sha256_after"]
    assert successor["write_completion_events_before"] == 1
    assert successor["write_completion_events_after"] == 1
    assert successor["successor_write_policy"]["allowed"] is False
    assert successor["ambiguous_step"]["status"] == "blocked"
    assert successor["ambiguous_step"]["target_exists"] is False
    assert successor["evidence_verification"]["ok"] is True
