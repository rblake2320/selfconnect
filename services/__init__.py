"""
services/ — Thin stateless abstraction layer over SelfConnect SDK internals.

Sits between SDK internals and external consumers (plugins, bridges, enterprise
transport). Not a daemon or web server — just a clean Python module layer.

All service classes are stateless (classmethod/staticmethod only).
"""

from services.agent_service import AgentService
from services.audio_service import AudioService
from services.pathbook_service import PathbookService
from services.policy_service import PolicyService

__all__ = ["AgentService", "AudioService", "PathbookService", "PolicyService"]
