from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from selfconnect_capabilities import Authority, CapabilityKernel, HostCollectors, KernelConfig
from selfconnect_capabilities.collectors import CollectedObservation


def _collectors() -> HostCollectors:
    return HostCollectors(
        mesh="test",
        window_reader=lambda **kwargs: [{
            "hwnd": 123,
            "pid": 456,
            "title": "SC Test",
            "exe_name": "WindowsTerminal.exe",
            "class_name": "CASCADIA_HOSTING_WINDOW_CLASS",
            "is_terminal": True,
            "visible": True,
            "raw_text": "must not escape",
        }],
        mesh_reader=lambda: {"ok": True, "agents": [{
            "role": "qwen",
            "birth_id": "birth",
            "generation": 1,
            "token": "must not escape",
        }]},
        platform_reader=lambda: {"ok": True, "win32": True},
    )


def test_window_and_mesh_collectors_emit_bounded_fields() -> None:
    collectors = _collectors()

    window = collectors.window_state()[0]
    mesh = collectors.mesh_state()[0]

    assert window.key == "windows.visible"
    assert window.value[0]["title"] == "SC Test"
    assert "raw_text" not in window.value[0]
    assert mesh.key == "mesh.test.roles"
    assert "token" not in mesh.value[0]


def test_process_collector_omits_command_lines_and_paths(monkeypatch) -> None:
    process = SimpleNamespace(info={
        "pid": 7,
        "name": "example.exe",
        "status": "running",
        "cmdline": ["secret"],
        "exe": "C:/private/example.exe",
    })
    monkeypatch.setattr(
        "selfconnect_capabilities.collectors.psutil.process_iter",
        lambda fields: [process],
    )

    result = _collectors().process_state()[0].value[0]

    assert result == {"pid": 7, "name": "example.exe", "status": "running"}


def test_gpu_collector_parses_bounded_nvidia_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(
        "selfconnect_capabilities.collectors.subprocess.check_output",
        lambda *args, **kwargs: "0, NVIDIA GeForce RTX 5090, 32607, 2011, 30596, 12\n",
    )

    result = _collectors().gpu_state()[0]

    assert result.source == "nvidia-smi"
    assert result.value[0]["memory_used_mb"] == 2011
    assert result.value[0]["utilization_percent"] == 12


def test_stale_query_refreshes_through_trusted_host_callback(tmp_path: Path) -> None:
    kernel = CapabilityKernel(
        KernelConfig(enabled=True, state_dir=tmp_path),
        Authority("qwen", frozenset({"read.state"})),
    )
    calls = []

    def refresh(prefix: str):
        calls.append(prefix)
        kernel.observe(
            "gpu.inventory",
            [{"memory_free_mb": 30000}],
            source="test-gpu",
            ttl_seconds=30,
        )
        return {"ok": True, "observed": ["gpu.inventory"]}

    kernel.register_state_refresher(refresh)

    result = kernel.execute("selfconnect.world-state", {"prefix": "gpu."})

    assert result["ok"] is True
    assert result["output"]["refreshed"] is True
    assert result["output"]["observations"][0]["source"] == "test-gpu"
    assert calls == ["gpu."]


def test_fresh_query_does_not_call_refresher(tmp_path: Path) -> None:
    kernel = CapabilityKernel(
        KernelConfig(enabled=True, state_dir=tmp_path),
        Authority("qwen", frozenset({"read.state"})),
    )
    kernel.observe("gpu.inventory", [], source="test", ttl_seconds=30)
    calls = []
    kernel.register_state_refresher(lambda prefix: calls.append(prefix) or {"ok": True})

    result = kernel.query_world_state(prefix="gpu.")

    assert result["refreshed"] is False
    assert calls == []


def test_collector_observation_can_be_written_by_trusted_runtime(tmp_path: Path) -> None:
    kernel = CapabilityKernel(
        KernelConfig(enabled=True, state_dir=tmp_path),
        Authority("runtime", frozenset({"read.state"})),
    )
    item = CollectedObservation("services.windows", [], "test-services", 10)

    kernel.observe(
        item.key,
        item.value,
        source=item.source,
        ttl_seconds=item.ttl_seconds,
    )

    assert kernel.world.get("services.windows")["source"] == "test-services"
