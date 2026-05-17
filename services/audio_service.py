"""
AudioService — Stable API for audio event operations.

Wraps selfconnect_audio's AudioEventBus for tone detection and event handling.
Gracefully degrades when audio dependencies are unavailable.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# selfconnect_audio lives one level up from this repo — try both locations
_AUDIO_AVAILABLE = False
_AudioEventBus = None

try:
    # Try sibling directory (PKA testing/selfconnect_audio/)
    _audio_path = str(Path(__file__).resolve().parent.parent.parent / "selfconnect_audio")
    if _audio_path not in sys.path:
        sys.path.insert(0, _audio_path)

    from selfconnect_audio import AudioEventBus as _Bus

    _AudioEventBus = _Bus
    _AUDIO_AVAILABLE = True
except ImportError:
    pass

if not _AUDIO_AVAILABLE:
    try:
        # Try as installed package
        from selfconnect_audio import AudioEventBus as _Bus  # type: ignore[no-redef]

        _AudioEventBus = _Bus
        _AUDIO_AVAILABLE = True
    except ImportError:
        log.info("selfconnect_audio not available — AudioService in no-op mode")


class AudioService:
    """Stable interface for audio event handling."""

    def __init__(self) -> None:
        self._bus: object | None = None
        self._running: bool = False

    @property
    def is_available(self) -> bool:
        """Whether the audio subsystem is importable and ready."""
        return _AUDIO_AVAILABLE

    def calibrate(self, tone_name: str, duration: int = 5) -> bool:
        """Calibrate a tone fingerprint for detection.

        Returns True if calibration succeeded, False otherwise.
        Currently a placeholder — full calibration requires WASAPI capture running.
        """
        if not _AUDIO_AVAILABLE:
            return False
        log.info("AudioService.calibrate: tone=%r duration=%d (stub)", tone_name, duration)
        return False

    def listen(self, callback: Callable[[str, float], None]) -> None:
        """Start listening for audio events (non-blocking).

        callback receives (event_name: str, confidence: float) on each detection.
        No-op if audio is unavailable.
        """
        if not _AUDIO_AVAILABLE or _AudioEventBus is None:
            return
        try:
            self._bus = _AudioEventBus()
            self._bus.subscribe("*", lambda topic, event: callback(topic, 1.0))
            self._bus.start()
            self._running = True
        except Exception as exc:
            log.debug("AudioService.listen failed: %s", exc)
            self._running = False

    def stop(self) -> None:
        """Stop listening for audio events. No-op if not running."""
        if self._bus is not None and self._running:
            try:
                self._bus.stop()
            except Exception as exc:
                log.debug("AudioService.stop failed: %s", exc)
            finally:
                self._running = False
                self._bus = None
