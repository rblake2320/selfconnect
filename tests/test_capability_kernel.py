from __future__ import annotations

import json
from pathlib import Path

import pytest
from selfconnect_capabilities import (
    Authority,
    CapabilityBroker,
    CapabilityKernel,
    KernelConfig,
    SkillManifest,
    SkillRegistry,
    TaskGraph,
    TaskStep,
)
from selfconnect_capabilities.evidence import EvidenceStore


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }


def _manifest(**overrides) -> SkillManifest:
    values = {
        "name": "test.echo",
        "version": "1.0.0",
        "description": "Echo a test value.",
        "adapter": "test-echo",
        "permissions": ("test.read",),
        "input_schema": _schema(),
        "verification": ("output-ok",),
        "tags": ("echo", "test"),
    }
    values.update(overrides)
    return SkillManifest(**values)


def test_manifest_digest_is_stable_and_detects_tampering() -> None:
    manifest = _manifest()
    pinned = manifest.public_dict()

    assert SkillManifest.from_dict(pinned).digest() == manifest.digest()
    pinned["description"] = "tampered"
    with pytest.raises(ValueError, match="digest mismatch"):
        SkillManifest.from_dict(pinned)


def test_manifest_digest_is_stable_across_json_line_endings() -> None:
    serialized = json.dumps(_manifest().public_dict(), indent=2)
    lf = SkillManifest.from_dict(json.loads(serialized.replace("\r\n", "\n")))
    crlf = SkillManifest.from_dict(json.loads(serialized.replace("\n", "\r\n")))

    expected = "ea1484b55dc6efa8491384d290074f28020c63ad4cc0f016c406ce535c29f8c5"
    assert lf.digest() == crlf.digest() == expected


def test_external_registry_requires_digest_pin(tmp_path: Path) -> None:
    path = tmp_path / "echo.json"
    path.write_text(json.dumps(_manifest().unsigned_dict()), encoding="utf-8")

    with pytest.raises(ValueError, match="not digest-pinned"):
        SkillRegistry().load_directory(tmp_path)


def test_discovery_reports_missing_authority() -> None:
    registry = SkillRegistry()
    registry.register(_manifest())

    results = registry.discover("echo value", Authority("qwen"), limit=3)

    assert results[0]["name"] == "test.echo"
    assert results[0]["available"] is False
    assert results[0]["missing_permissions"] == ["test.read"]


def test_broker_executes_only_registered_adapter_with_evidence(tmp_path: Path) -> None:
    registry = SkillRegistry()
    registry.register(_manifest())
    evidence = EvidenceStore(tmp_path / "evidence.jsonl")
    broker = CapabilityBroker(registry, evidence)
    broker.register_adapter("test-echo", lambda value: {"ok": True, "value": value})
    broker.register_verifier("output-ok", lambda arguments, output: {"ok": output.get("ok") is True})

    result = broker.execute("test.echo", {"value": "hello"}, Authority("qwen", frozenset({"test.read"})))

    assert result.ok is True
    assert result.output["value"] == "hello"
    assert evidence.verify()["ok"] is True


def test_broker_denies_without_calling_adapter(tmp_path: Path) -> None:
    calls = []
    registry = SkillRegistry()
    registry.register(_manifest())
    evidence = EvidenceStore(tmp_path / "evidence.jsonl")
    broker = CapabilityBroker(registry, evidence)
    broker.register_adapter("test-echo", lambda value: calls.append(value) or {"ok": True})

    result = broker.execute("test.echo", {"value": "no"}, Authority("qwen"))

    assert result.ok is False
    assert "missing capability permissions" in result.output["error"]
    assert calls == []
    events = [
        json.loads(line)["event"]
        for line in evidence.path.read_text(encoding="utf-8").splitlines()
    ]
    assert events == ["capability_policy_decision", "capability_denied"]


def test_broker_authorize_records_denial_without_model_tool_call(tmp_path: Path) -> None:
    registry = SkillRegistry()
    registry.register(_manifest())
    evidence = EvidenceStore(tmp_path / "evidence.jsonl")
    broker = CapabilityBroker(registry, evidence)

    decision = broker.authorize("test.echo", Authority("qwen"))

    assert decision["allowed"] is False
    assert decision["missing_permissions"] == ["test.read"]
    row = json.loads(evidence.path.read_text(encoding="utf-8"))
    assert row["event"] == "capability_policy_decision"
    assert row["details"]["allowed"] is False


def test_broker_rejects_unknown_arguments_before_adapter(tmp_path: Path) -> None:
    registry = SkillRegistry()
    registry.register(_manifest())
    broker = CapabilityBroker(registry, EvidenceStore(tmp_path / "evidence.jsonl"))
    broker.register_adapter("test-echo", lambda value: {"ok": True})

    with pytest.raises(ValueError, match="unexpected skill arguments"):
        broker.execute(
            "test.echo",
            {"value": "yes", "command": "not allowed"},
            Authority("qwen", frozenset({"test.read"})),
        )


def test_evidence_chain_detects_tampering_and_redacts_content(tmp_path: Path) -> None:
    evidence = EvidenceStore(tmp_path / "evidence.jsonl")
    evidence.append("one", **{
        "content": "sensitive body",
        "token": "sensitive value",
        "safe": "visible",
    })
    evidence.append("two", ok=True)
    rows = evidence.path.read_text(encoding="utf-8").splitlines()
    first = json.loads(rows[0])

    assert first["details"]["content"] == "[redacted]"
    assert first["details"]["token"] == "[redacted]"
    assert evidence.verify()["ok"] is True

    first["details"]["safe"] = "changed"
    rows[0] = json.dumps(first)
    evidence.path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert evidence.verify()["ok"] is False


def test_task_graph_dependencies_and_resume(tmp_path: Path) -> None:
    path = tmp_path / "task.json"
    graph = TaskGraph(path, goal="diagnose app")
    inspect = TaskStep.create("selfconnect.doctor", {}, step_id="inspect")
    repair = TaskStep.create("selfconnect.command", {"argv": ["repair"]}, depends_on=("inspect",), step_id="repair")
    graph.add(inspect)
    graph.add(repair)

    assert [step.step_id for step in graph.ready()] == ["inspect"]
    graph.transition("inspect", "running")
    graph.transition("inspect", "completed", {"ok": True})
    assert [step.step_id for step in graph.ready()] == ["repair"]

    resumed = TaskGraph.load(path)
    assert resumed.steps["inspect"].status == "completed"
    assert resumed.steps["inspect"].attempts == 1


def test_task_graph_rejects_invalid_transition(tmp_path: Path) -> None:
    graph = TaskGraph(tmp_path / "task.json")
    graph.add(TaskStep.create("selfconnect.doctor", {}, step_id="one"))

    with pytest.raises(ValueError, match="invalid task transition"):
        graph.transition("one", "completed")


def test_kernel_feature_flag_and_builtin_execution(tmp_path: Path) -> None:
    disabled = CapabilityKernel(KernelConfig(state_dir=tmp_path), Authority("qwen"))
    with pytest.raises(RuntimeError, match="disabled"):
        disabled.discover("window")

    kernel = CapabilityKernel(
        KernelConfig(enabled=True, dynamic_skills=True, state_dir=tmp_path),
        Authority("qwen", frozenset({"observe.system"})),
    )
    kernel.bind_adapter("doctor", lambda: {"ok": True, "capabilities": ["win32"]})

    assert kernel.discover("diagnostic capabilities")["skills"][0]["name"] == "selfconnect.doctor"
    assert kernel.execute("selfconnect.doctor", {})["ok"] is True


def test_kernel_runs_and_resumes_ready_task_steps(tmp_path: Path) -> None:
    kernel = CapabilityKernel(
        KernelConfig(enabled=True, task_graphs=True, state_dir=tmp_path),
        Authority("qwen", frozenset({"observe.system"})),
    )
    kernel.bind_adapter("doctor", lambda: {"ok": True})
    graph = kernel.new_task("inspect machine", task_id="task-one")
    graph.add(TaskStep.create("selfconnect.doctor", {}, step_id="doctor"))

    result = kernel.run_ready(graph)
    resumed = kernel.load_task("task-one")

    assert result["executed"][0]["status"] == "completed"
    assert resumed.summary()["complete"] is True
    assert kernel.evidence.verify()["ok"] is True


def test_resumed_task_rederives_current_authority(tmp_path: Path) -> None:
    privileged = CapabilityKernel(
        KernelConfig(enabled=True, task_graphs=True, state_dir=tmp_path),
        Authority("creator", frozenset({"observe.system"})),
    )
    graph = privileged.new_task("inspect later", task_id="authority-resume")
    graph.add(TaskStep.create("selfconnect.doctor", {}, step_id="doctor"))

    restricted = CapabilityKernel(
        KernelConfig(enabled=True, task_graphs=True, state_dir=tmp_path),
        Authority("resumer"),
    )
    restricted.bind_adapter("doctor", lambda: {"ok": True})
    resumed = restricted.load_task("authority-resume")
    result = restricted.run_ready(resumed)

    assert result["executed"][0]["status"] == "blocked"
    assert result["executed"][0]["result"]["verification"]["reason"] == "permission_denied"


def test_kernel_loads_only_digest_pinned_external_skills(monkeypatch, tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    manifest = _manifest()
    (skills / "echo.json").write_text(
        json.dumps(manifest.public_dict()),
        encoding="utf-8",
    )
    monkeypatch.setenv("SC_CAPABILITY_SKILL_PATHS", str(skills))
    monkeypatch.setenv("SC_CAPABILITY_KERNEL", "1")

    config = KernelConfig.from_env(tmp_path / "state")
    kernel = CapabilityKernel(config, Authority("qwen"))

    assert kernel.registry.get("test.echo").digest() == manifest.digest()
