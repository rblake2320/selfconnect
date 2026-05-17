"""
PathbookService — Stable API for FRP (Failure Remediation Protocol) operations.

Wraps frp_client for lookup and contribution of verified fix paths.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

try:
    from frp_client import (
        compute_fingerprint,
        contribute_frp,
        lookup_frp,
    )

    _FRP_AVAILABLE = True
except ImportError:
    _FRP_AVAILABLE = False
    log.warning("frp_client not importable — PathbookService degraded to no-op mode")


class PathbookService:
    """Stable interface for FRP pathbook lookup and contribution."""

    @property
    def is_available(self) -> bool:
        """Whether the FRP backend is importable."""
        return _FRP_AVAILABLE

    def lookup(self, error_text: str, env_class: str | None = None) -> dict | None:
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

    def contribute(
        self,
        title: str,
        error_text: str,
        env_class: str,
        fix_steps: list[str],
        *,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict | None:
        """Contribute a verified fix path to the pathbook registry.

        Returns the created entry dict on success, None on failure.
        Logs all contributions for audit trail.
        """
        if not _FRP_AVAILABLE:
            log.warning("PathbookService.contribute called but frp_client unavailable")
            return None
        try:
            log.info(
                "PathbookService: contributing fix — title=%r env_class=%r",
                title,
                env_class,
            )
            result = contribute_frp(
                title=title,
                error_text=error_text,
                env_class=env_class,
                fix_steps=fix_steps,
                tags=tags or [],
                metadata=metadata or {},
            )
            log.info("PathbookService: contribution accepted — id=%s", result.get("id"))
            return result
        except Exception as exc:
            log.warning("PathbookService.contribute failed: %s", exc)
            return None

    def fingerprint(self, error_text: str, env_class: str) -> str:
        """Compute the FRP fingerprint for an error + environment pair.

        Returns empty string if frp_client unavailable.
        """
        if not _FRP_AVAILABLE:
            return ""
        try:
            return compute_fingerprint(error_text, env_class)
        except Exception as exc:
            log.debug("PathbookService.fingerprint failed: %s", exc)
            return ""
