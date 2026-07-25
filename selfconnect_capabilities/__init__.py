"""SelfConnect Capability Kernel.

The kernel gives models progressive skill discovery while keeping execution,
permissions, and verification outside the model process.
"""

from .broker import CapabilityBroker, CapabilityResult
from .collectors import CollectedObservation, HostCollectors
from .kernel import CapabilityKernel, KernelConfig
from .mcp_bridge import (
    MCPBridge,
    MCPServerConfig,
    MCPSchemaTrustStore,
    MCPToolDescriptor,
    StdioMCPClient,
)
from .models import SkillManifest
from .permissions import Authority, PermissionDenied
from .registry import SkillRegistry
from .task_graph import TaskGraph, TaskStep
from .world_state import Observation, WorldStateStore

__all__ = [
    "Authority",
    "CapabilityBroker",
    "CapabilityKernel",
    "CapabilityResult",
    "CollectedObservation",
    "HostCollectors",
    "KernelConfig",
    "MCPBridge",
    "MCPServerConfig",
    "MCPSchemaTrustStore",
    "MCPToolDescriptor",
    "Observation",
    "PermissionDenied",
    "SkillManifest",
    "SkillRegistry",
    "StdioMCPClient",
    "TaskGraph",
    "TaskStep",
    "WorldStateStore",
]
