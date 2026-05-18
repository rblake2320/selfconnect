"""
PathbookService — Thin wrapper around FRP (Failure Remediation Protocol).

Uses frp_client.py for pathbook lookup and contribution.
Gracefully degrades when frp_client is not importable. Never raises on lookup failure.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# frp_client.py lives in the parent directory
_parent_dir = str(Path(__file__).resolve().parent.parent)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

_FRP_AVAILABLE = False

try:
    from frp_client import compute_fingerprint, contribute_frp, lookup_frp

    _FRP_AVAILABLE = True
except ImportError:
    _FRP_AVAILABLE = False
    log.warning("frp_client not importable — PathbookService in no-op mode")


class PathbookService:
    """Stateless interface for FRP pathbook lookup and contribution."""

    @staticmethod
    def lookup(error_text: str, env_class: str | None = None) -> dict | None:
        """Look up a known fix path for the given error.

        Returns the FRP entry dict if found, None otherwise.
        Never raises — returns None on any failure.
        """
        if not _FRP_AVAILABLE:
            return None
        try:
            return lookup_frp(error_text, env_class)
        except Exception as exc:
            log.debug("PathbookService.lookup failed: %s", exc)
            return None

    @staticmethod
    def contribute(
        title: str,
        error_text: str,
        env_class: str,
        fix_steps: list[str],
        *,
        failed_attempts: list[str] | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        """Contribute a verified fix path to the pathbook registry.

        Returns the created entry dict on success, or empty dict on failure.
        """
        if not _FRP_AVAILABLE:
            log.warning("PathbookService.contribute: frp_client unavailable")
            return {}
        try:
            result = contribute_frp(
                title=title,
                error_text=error_text,
                env_class=env_class,
                fix_steps=fix_steps,
                tags=tags or [],
                metadata=metadata or {},
            )
            return result if result else {}
        except Exception as exc:
            log.debug("PathbookService.contribute failed: %s", exc)
            return {}

    @staticmethod
    def fingerprint(error_text: str, env_class: str) -> str:
        """Compute the FRP fingerprint for an error + environment pair.

        Returns the SHA-256 hex string, or empty string if unavailable.
        """
        if not _FRP_AVAILABLE:
            return ""
        try:
            return compute_fingerprint(error_text, env_class)
        except Exception as exc:
            log.debug("PathbookService.fingerprint failed: %s", exc)
            return ""
