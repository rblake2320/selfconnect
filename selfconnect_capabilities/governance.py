"""Mechanical Capability OS governance-profile admission."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GovernanceInputs:
    kernel_enabled: bool
    dynamic_skills: bool
    task_graphs: bool
    skill_learning: str
    visual_specialist: bool
    mcp_servers: int
    allow_input: bool
    allow_writes: bool
    allow_commands: bool


def evaluate_governance(profile: str, inputs: GovernanceInputs) -> dict[str, Any]:
    """Return deterministic admission; no model can waive these requirements."""
    mode = profile.strip().casefold()
    if mode not in {"observe", "governed", "restricted"}:
        raise ValueError(f"unknown Capability OS governance profile: {profile}")
    violations: list[str] = []
    if mode in {"governed", "restricted"}:
        required = {
            "kernel_enabled": inputs.kernel_enabled,
            "dynamic_skills": inputs.dynamic_skills,
            "task_graphs": inputs.task_graphs,
            "shadow_skill_learning": inputs.skill_learning == "shadow",
            "visual_specialist": inputs.visual_specialist,
        }
        violations.extend(name for name, satisfied in required.items() if not satisfied)
    if mode == "restricted":
        prohibited = {
            "window_input_enabled": inputs.allow_input,
            "file_writes_enabled": inputs.allow_writes,
            "commands_enabled": inputs.allow_commands,
            "external_mcp_configured": inputs.mcp_servers > 0,
        }
        violations.extend(name for name, present in prohibited.items() if present)
    return {
        "ok": not violations,
        "profile": mode,
        "violations": sorted(violations),
        "requirements_enforced_by": "trusted-runtime",
        "model_override_allowed": False,
    }
