"""
PolicyService — Thin wrapper around the approval/policy rules engine.

Uses approval_partner.py for tool call allow/deny decisions.
Gracefully degrades when approval_partner is not importable.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# approval_partner.py lives in the parent directory
_parent_dir = str(Path(__file__).resolve().parent.parent)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

_POLICY_AVAILABLE = False
_DEFAULT_ALLOW: list[str] = []
_DEFAULT_DENY: list[str] = []

try:
    from approval_partner import (
        DEFAULT_ALLOW,
        DEFAULT_DENY,
        PartnerConfig,
        decide,
        extract_tool_call,
    )

    _POLICY_AVAILABLE = True
    _DEFAULT_ALLOW = list(DEFAULT_ALLOW)
    _DEFAULT_DENY = list(DEFAULT_DENY)
except ImportError:
    _POLICY_AVAILABLE = False
    log.warning("approval_partner not importable — PolicyService in allow-all mode")


class PolicyService:
    """Stateless interface for tool approval policy decisions."""

    @staticmethod
    def decide(tool_call: str) -> bool | None:
        """Evaluate a tool call string against policy rules.

        Returns:
            True  — ALLOW (tool matches allow list)
            False — DENY (tool matches deny list)
            None  — UNKNOWN (requires escalation)
        """
        if not _POLICY_AVAILABLE:
            log.debug("PolicyService: degraded mode, returning None for %r", tool_call)
            return None
        try:
            extracted = extract_tool_call(tool_call)
            return decide(extracted, PartnerConfig())
        except Exception as exc:
            log.debug("PolicyService.decide failed: %s", exc)
            return None

    @staticmethod
    def is_allowed(tool_call: str) -> bool:
        """Convenience: True if tool call is explicitly allowed, False otherwise.

        Maps None (unknown) to False for safety.
        """
        result = PolicyService.decide(tool_call)
        return result is True

    @staticmethod
    def get_allow_list() -> list[str]:
        """Return the current allow-list patterns.

        Returns empty list if policy engine is unavailable.
        """
        return list(_DEFAULT_ALLOW)

    @staticmethod
    def get_deny_list() -> list[str]:
        """Return the current deny-list patterns.

        Returns empty list if policy engine is unavailable.
        """
        return list(_DEFAULT_DENY)
