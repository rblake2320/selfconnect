"""
PolicyService — Stable API for tool approval decisions.

Wraps approval_partner for policy-gated tool call evaluation.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

try:
    from approval_partner import PartnerConfig, decide, extract_tool_call

    _POLICY_AVAILABLE = True
except ImportError:
    _POLICY_AVAILABLE = False
    log.warning(
        "approval_partner not importable — PolicyService degraded to allow-all mode"
    )


class PolicyService:
    """Stable interface for tool approval policy decisions."""

    def __init__(self, config: PartnerConfig | None = None) -> None:
        if _POLICY_AVAILABLE and config is None:
            self._config = PartnerConfig()
        elif _POLICY_AVAILABLE:
            self._config = config
        else:
            self._config = None

    @property
    def is_available(self) -> bool:
        """Whether the policy engine is importable."""
        return _POLICY_AVAILABLE

    def evaluate(self, tool_call: str) -> bool | None:
        """Evaluate a tool call string against policy rules.

        Returns:
            True  — ALLOW (tool is in allow list)
            False — DENY (tool is in deny list)
            None  — UNKNOWN (requires escalation)
        """
        if not _POLICY_AVAILABLE:
            log.debug("PolicyService: degraded mode, allowing %r", tool_call)
            return True
        try:
            extracted = extract_tool_call(tool_call)
            return decide(extracted, self._config)
        except Exception as exc:
            log.debug("PolicyService.evaluate failed: %s", exc)
            return None

    def is_allowed(self, tool_call: str) -> bool:
        """Return True if tool call is explicitly allowed."""
        return self.evaluate(tool_call) is True

    def is_denied(self, tool_call: str) -> bool:
        """Return True if tool call is explicitly denied."""
        return self.evaluate(tool_call) is False

    def requires_escalation(self, tool_call: str) -> bool:
        """Return True if tool call result is unknown (needs human review)."""
        return self.evaluate(tool_call) is None
