"""Integrated M7 tests using real filesystem and runtime components."""

from __future__ import annotations

import tomllib
import uuid
from pathlib import Path

import pytest
import sc_local_agent_runtime as runtime_mod
from selfconnect_capabilities import Authority, CapabilityKernel, KernelConfig
from selfconnect_capabilities.governance import GovernanceInputs, evaluate_governance
from selfconnect_capabilities.visual_specialist import VISUAL_OBSERVE_SKILL


def test_task_meta_plane_executes_digest_bound_real_file_reads(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    first = repository / "first.txt"
    second = repository / "second.txt"
    first.write_text("first real task step", encoding="utf-8")
    second.write_text("second real task step", encoding="utf-8")
    tools = runtime_mod.SelfConnectTools(
        runtime_mod.RuntimeConfig(repo_root=repository)
    )
    kernel = CapabilityKernel(
        KernelConfig(
            enabled=True,
            dynamic_skills=True,
            task_graphs=True,
            state_dir=tmp_path / "state",
        ),
        Authority("m7-task-test", frozenset({"read.file"})),
        task_owner="m7-task-owner",
    )
    kernel.bind_adapter("file-read", tools.file_read)
    plan = kernel.create_task_plan(
        "Read two owned files in order.",
        [
            {
                "step_id": "first",
                "capability": "selfconnect.file-read",
                "arguments": {"path": str(first)},
            },
            {
                "step_id": "second",
                "capability": "selfconnect.file-read",
                "arguments": {"path": str(second)},
                "depends_on": ["first"],
            },
        ],
    )
    task_id = plan["task"]["task_id"]
    first_run = kernel.continue_task_plan(task_id, max_steps=1)
    second_run = kernel.continue_task_plan(task_id, max_steps=1)

    assert first_run["executed"][0]["step_id"] == "first"
    assert second_run["executed"][0]["step_id"] == "second"
    assert second_run["task"]["complete"] is True
    assert second_run["task"]["statuses"] == {"completed": 2}
    assert kernel.evidence.verify()["ok"] is True


def test_task_meta_plane_rejects_smuggling_and_forward_dependencies(tmp_path: Path) -> None:
    kernel = CapabilityKernel(
        KernelConfig(enabled=True, task_graphs=True, state_dir=tmp_path),
        Authority("m7-task-test", frozenset({"read.file"})),
    )
    with pytest.raises(ValueError, match="unexpected task step fields"):
        kernel.create_task_plan(
            "Smuggle authority.",
            [{
                "capability": "selfconnect.file-read",
                "arguments": {"path": "x"},
                "permissions": ["execute.command"],
            }],
        )
    with pytest.raises(ValueError, match="earlier step ids"):
        kernel.create_task_plan(
            "Forward dependency.",
            [{
                "step_id": "first",
                "capability": "selfconnect.file-read",
                "arguments": {"path": "x"},
                "depends_on": ["later"],
            }],
        )
    with pytest.raises(ValueError, match="step id must be"):
        kernel.create_task_plan(
            "Reject type coercion.",
            [{
                "step_id": 1,
                "capability": "selfconnect.file-read",
                "arguments": {"path": "x"},
            }],
        )
    assert list((tmp_path / "tasks").glob("*.json")) == []


def test_dynamic_task_mode_has_exactly_five_meta_tools() -> None:
    schemas = runtime_mod.tool_schemas(
        capability_kernel=True,
        dynamic_skills=True,
        task_graphs=True,
    )
    assert [item["function"]["name"] for item in schemas] == [
        "capability_discover",
        "capability_inspect",
        "capability_execute",
        "capability_task_create",
        "capability_task_continue",
    ]


def test_governance_profiles_fail_closed() -> None:
    incomplete = GovernanceInputs(
        kernel_enabled=True,
        dynamic_skills=False,
        task_graphs=False,
        skill_learning="off",
        visual_specialist=False,
        mcp_servers=0,
        allow_input=False,
        allow_writes=False,
        allow_commands=False,
    )
    governed = evaluate_governance("governed", incomplete)
    assert governed["ok"] is False
    assert governed["model_override_allowed"] is False
    assert governed["violations"] == [
        "dynamic_skills",
        "shadow_skill_learning",
        "task_graphs",
        "visual_specialist",
    ]

    restricted = evaluate_governance(
        "restricted",
        GovernanceInputs(
            kernel_enabled=True,
            dynamic_skills=True,
            task_graphs=True,
            skill_learning="shadow",
            visual_specialist=True,
            mcp_servers=1,
            allow_input=True,
            allow_writes=True,
            allow_commands=True,
        ),
    )
    assert restricted["ok"] is False
    assert restricted["violations"] == [
        "commands_enabled",
        "external_mcp_configured",
        "file_writes_enabled",
        "window_input_enabled",
    ]


def test_governed_runtime_integrates_visual_skill_without_loading_models(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Environment redirection selects real isolated state stores; it does not
    # replace any runtime, Win32, GPU, model, or broker function.
    monkeypatch.setenv("SC_CAPABILITY_KERNEL", "1")
    monkeypatch.setenv("SC_DYNAMIC_SKILLS", "1")
    monkeypatch.setenv("SC_TASK_GRAPH", "1")
    monkeypatch.setenv("SC_SKILL_LEARNING", "shadow")
    monkeypatch.setenv("SC_VISUAL_SPECIALIST", "1")
    monkeypatch.setenv("SC_CAPABILITY_GOVERNANCE_PROFILE", "governed")
    monkeypatch.setenv("SC_CAPABILITY_STATE_DIR", str(tmp_path / "capabilities"))
    monkeypatch.setenv("SC_LOCAL_AGENT_STATE_DIR", str(tmp_path / "activity"))
    monkeypatch.setenv("SELFCONNECT_LOCAL_MODEL_DIR", str(tmp_path / "roles"))
    monkeypatch.setenv("SELFCONNECT_MESH_DIR", str(tmp_path / "mesh"))
    role = f"m7-real-runtime-{uuid.uuid4().hex[:8]}"
    runtime = runtime_mod.LocalAgentRuntime(
        runtime_mod.RuntimeConfig(
            role=role,
            mesh="m7-integration",
            model="qwen3.6:27b",
            repo_root=Path(__file__).resolve().parents[1],
        )
    )

    assert runtime.governance["ok"] is True
    assert runtime.kernel.registry.get(
        VISUAL_OBSERVE_SKILL.name
    ).digest() == VISUAL_OBSERVE_SKILL.digest()
    assert runtime.kernel.inspect(VISUAL_OBSERVE_SKILL.name)["skill"]["available"] is True
    assert runtime.kernel.shadow_compiler is not None


def test_capability_os_install_extra_and_migration_entrypoint_are_packaged() -> None:
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = set(project["project"]["optional-dependencies"]["capability-os"])
    assert {
        "cryptography>=42.0.0",
        "pywin32>=306",
        "pywinauto>=0.6.8",
        "comtypes>=1.4.0",
        "pytesseract>=0.3.10",
        "mcp>=1.0.0",
    } == dependencies
    assert project["project"]["scripts"]["selfconnect-capabilities-migrate"] == (
        "sc_capability_migrate:main"
    )
    assert "sc_capability_migrate.py" in project["tool"]["hatch"]["build"]["targets"][
        "wheel"
    ]["include"]
