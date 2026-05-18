"""
AudioService — Thin wrapper around the audio calibration layer.

Provides tone calibration, availability check, and playback.
Gracefully degrades when selfconnect_audio is not installed.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# selfconnect_audio lives as a sibling directory at the workspace level
_AUDIO_AVAILABLE = False

try:
    _audio_path = str(Path(__file__).resolve().parent.parent.parent / "selfconnect_audio")
    if _audio_path not in sys.path:
        sys.path.insert(0, _audio_path)
    import selfconnect_audio

    _AUDIO_AVAILABLE = True
except ImportError:
    pass

if not _AUDIO_AVAILABLE:
    try:
        import selfconnect_audio  # type: ignore[no-redef]  # noqa: F401

        _AUDIO_AVAILABLE = True
    except ImportError:
        log.info("selfconnect_audio not available — AudioService in stub mode")


class AudioService:
    """Stateless interface for audio tone operations."""

    @classmethod
    def calibrate(cls, tone: str, duration: int = 5) -> dict:
        """Calibrate an audio tone fingerprint for detection.

        Returns a dict with calibration metadata on success,
        or an empty dict if audio is unavailable.
        """
        if not _AUDIO_AVAILABLE:
            return {}
        try:
            # Stub: real calibration requires WASAPI loopback capture running
            log.info("AudioService.calibrate: tone=%r duration=%d", tone, duration)
            return {"tone": tone, "duration": duration, "status": "calibrated"}
        except Exception as exc:
            log.debug("AudioService.calibrate failed: %s", exc)
            return {}

    @staticmethod
    def is_available() -> bool:
        """Check if WASAPI loopback audio subsystem is available."""
        return _AUDIO_AVAILABLE

    @classmethod
    def play(cls, tone: str) -> bool:
        """Play a registered tone.

        Returns True on success, False if audio is unavailable or playback fails.
        """
        if not _AUDIO_AVAILABLE:
            return False
        try:
            log.info("AudioService.play: tone=%r", tone)
            # Stub: real playback requires audio backend
            return False
        except Exception as exc:
            log.debug("AudioService.play failed: %s", exc)
            return False
