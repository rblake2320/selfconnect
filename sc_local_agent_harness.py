"""Model profiles and deterministic tool contracts for local agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HarnessProfile:
    name: str
    temperature: float | None = None
    seed: int | None = None
    system_suffix: str = ""
    tool_description_overrides: dict[str, str] | None = None


@dataclass(frozen=True)
class ToolContract:
    """A controller-supplied completion contract, not a prompt-derived guess."""

    required_tools: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] | None = None
    ordered: bool = True
    max_retries: int = 1
    label: str = "controller"

    def __post_init__(self) -> None:
        required = tuple(dict.fromkeys(self.required_tools))
        allowed = None if self.allowed_tools is None else tuple(dict.fromkeys(self.allowed_tools))
        if allowed is not None and not set(required).issubset(allowed):
            raise ValueError("required tools must be a subset of allowed tools")
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        object.__setattr__(self, "required_tools", required)
        object.__setattr__(self, "allowed_tools", allowed)

    def validate(self, called: list[str], rejected: list[str] | None = None) -> dict[str, Any]:
        rejected = rejected or []
        missing = [name for name in self.required_tools if name not in called]
        unexpected = list(dict.fromkeys(rejected))
        if self.allowed_tools is not None:
            unexpected.extend(name for name in called if name not in self.allowed_tools)
            unexpected = list(dict.fromkeys(unexpected))
        order_ok = True
        if self.ordered and self.required_tools:
            positions = [called.index(name) for name in self.required_tools if name in called]
            order_ok = len(positions) == len(self.required_tools) and positions == sorted(positions)
        return {
            "ok": not missing and not unexpected and order_ok,
            "missing": missing,
            "unexpected": unexpected,
            "order_ok": order_ok,
        }

    def instruction(self) -> str:
        required = ", ".join(self.required_tools) or "none"
        allowed = "runtime tool set" if self.allowed_tools is None else (", ".join(self.allowed_tools) or "none")
        return (
            "\n\n<selfconnect-tool-contract>\n"
            f"source={self.label}; required={required}; allowed={allowed}; "
            f"required_order={'yes' if self.ordered else 'no'}.\n"
            "This is enforced runtime policy. Call every required tool even when its result seems "
            "predictable or a permission gate is expected to deny it; the attempted call is audit "
            "evidence. Do not claim completion before the calls finish. Do not narrate tool use.\n"
            "</selfconnect-tool-contract>"
        )

    def correction(self, validation: dict[str, Any]) -> str:
        missing = ", ".join(validation["missing"]) or "none"
        return (
            "<selfconnect-contract-retry>"
            f"Required tool evidence is missing: {missing}. Call the missing tool(s) now in the "
            "required order. Return no planning or narration."
            "</selfconnect-contract-retry>"
        )


QWEN_TOOL_OVERRIDES = {
    "verify_role_window": (
        "Verify a registered role's current HWND identity. Invoke when verification is requested, "
        "including for a role believed to be absent; only the returned result is evidence."
    ),
    "send_role_message": (
        "Attempt a guarded send to a registered mesh role. Invoke when an explicit send attempt is "
        "requested even if input is probably disabled; never infer delivery from configuration."
    ),
    "file_write": (
        "Attempt a repository-bounded write. Invoke when an explicit write attempt is requested even "
        "if writes are probably disabled; report the returned gate result."
    ),
    "command": (
        "Attempt an argv-form command. Invoke when an explicit command attempt is requested even if "
        "execution is probably disabled; report the returned gate result."
    ),
}

QWEN_PROFILE = HarnessProfile(
    name="qwen3.6-selfconnect-v1",
    temperature=0.0,
    seed=42,
    system_suffix=(
        "\nQwen harness profile: treat tool calls as auditable actions. When the user or a runtime "
        "contract explicitly requests a tool action, call it rather than predicting its result. "
        "A disabled or failed tool result is valid evidence. Never emit tool-call XML or imitate a "
        "tool result in prose. After tools finish, answer concisely without a play-by-play."
    ),
    tool_description_overrides=QWEN_TOOL_OVERRIDES,
)

GENERIC_PROFILE = HarnessProfile(name="generic-selfconnect-v1")
OFF_PROFILE = HarnessProfile(name="off")


def resolve_harness_profile(model: str, requested: str = "auto") -> HarnessProfile:
    choice = requested.strip().casefold()
    if choice in {"off", "raw", "none"}:
        return OFF_PROFILE
    if choice in {"qwen", "qwen3.6"}:
        return QWEN_PROFILE
    if choice not in {"", "auto", "generic"}:
        raise ValueError(f"unknown local-agent harness profile: {requested}")
    if choice != "generic" and model.strip().casefold().startswith("qwen3.6:"):
        return QWEN_PROFILE
    return GENERIC_PROFILE
