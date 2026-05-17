"""
services/ — Stable abstraction layer over SelfConnect SDK internals.

Plugins and audio sidecars call these service APIs instead of SDK internals
directly. This prevents breaking changes as new surfaces are added.

v0.10.1 — Layer 6 Service Abstraction
"""

from services.agent_service import AgentService
from services.audio_service import AudioService
from services.pathbook_service import PathbookService
from services.policy_service import PolicyService

__all__ = ["AgentService", "AudioService", "PathbookService", "PolicyService"]
