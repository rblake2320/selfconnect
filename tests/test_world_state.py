from __future__ import annotations

from pathlib import Path

from selfconnect_capabilities import (
    Authority,
    CapabilityKernel,
    KernelConfig,
    WorldStateStore,
)


def test_world_state_tracks_freshness_source_and_confidence(tmp_path: Path) -> None:
    store = WorldStateStore(tmp_path)
    store.observe(
        "gpu.0.memory",
        {"used_mb": 1200},
        source="nvidia-smi",
        confidence=1.0,
        ttl_seconds=10,
        now=100,
    )

    fresh = store.get("gpu.0.memory", now=105)
    stale = store.get("gpu.0.memory", now=111)

    assert fresh is not None
    assert fresh["source"] == "nvidia-smi"
    assert fresh["confidence"] == 1.0
    assert fresh["fresh"] is True
    assert stale is None
    assert store.get("gpu.0.memory", include_stale=True, now=111)["fresh"] is False


def test_sensitive_observation_stores_digest_not_value(tmp_path: Path) -> None:
    store = WorldStateStore(tmp_path)
    observation = store.observe(
        "credential.example",
        {"token": "do-not-store"},
        source="test",
        sensitive=True,
        now=100,
    )
    stored = store.get("credential.example", now=101)

    assert observation.value == "[sensitive]"
    assert stored["value"] == "[sensitive]"
    assert "do-not-store" not in store.path.read_text(encoding="utf-8")
    assert stored["value_digest"]


def test_world_state_change_feed_records_replacements(tmp_path: Path) -> None:
    store = WorldStateStore(tmp_path)
    first = store.observe("service.discord", "stopped", source="service-manager", now=100)
    second = store.observe("service.discord", "running", source="service-manager", now=110)

    changes = store.changes(since=105)["changes"]

    assert len(changes) == 1
    assert changes[0]["value_digest"] == second.value_digest
    assert changes[0]["previous_digest"] == first.value_digest


def test_world_state_capability_is_permission_gated(tmp_path: Path) -> None:
    config = KernelConfig(enabled=True, state_dir=tmp_path)
    denied = CapabilityKernel(config, Authority("denied"))
    denied.observe("runtime.test", {"model": "qwen"}, source="runtime")

    denied_result = denied.execute("selfconnect.world-state", {"prefix": "runtime."})

    allowed = CapabilityKernel(config, Authority("allowed", frozenset({"read.state"})))
    allowed_result = allowed.execute("selfconnect.world-state", {"prefix": "runtime."})

    assert denied_result["ok"] is False
    assert allowed_result["ok"] is True
    assert allowed_result["output"]["observations"][0]["key"] == "runtime.test"


def test_kernel_observation_links_to_evidence(tmp_path: Path) -> None:
    kernel = CapabilityKernel(
        KernelConfig(enabled=True, state_dir=tmp_path),
        Authority("host", frozenset({"read.state"})),
    )

    result = kernel.observe(
        "mesh.default.roles",
        [{"role": "qwen"}],
        source="mesh-registry",
        ttl_seconds=15,
    )

    assert result["observation"]["evidence_id"]
    assert kernel.evidence.verify()["ok"] is True
