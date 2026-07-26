"""SelfConnect Capability Kernel.

The kernel gives models progressive skill discovery while keeping execution,
permissions, and verification outside the model process.
"""

from .broker import CapabilityBroker, CapabilityResult
from .collectors import CollectedObservation, HostCollectors
from .governance import GovernanceInputs, evaluate_governance
from .kernel import CapabilityKernel, KernelConfig
from .mcp_bridge import (
    MCPBridge,
    MCPSchemaTrustStore,
    MCPServerConfig,
    MCPToolDescriptor,
    StdioMCPClient,
)
from .models import SkillManifest
from .permissions import Authority, PermissionDenied
from .registry import SkillRegistry
from .shadow_compiler import ShadowSkillCompiler
from .task_graph import TaskGraph, TaskStep
from .world_state import Observation, WorldStateStore

__all__ = [
    "Authority",
    "CapabilityBroker",
    "CapabilityKernel",
    "CapabilityResult",
    "CollectedObservation",
    "GovernanceInputs",
    "HostCollectors",
    "KernelConfig",
    "MCPBridge",
    "MCPSchemaTrustStore",
    "MCPServerConfig",
    "MCPToolDescriptor",
    "Observation",
    "PermissionDenied",
    "ShadowSkillCompiler",
    "SkillManifest",
    "SkillRegistry",
    "StdioMCPClient",
    "TaskGraph",
    "TaskStep",
    "WorldStateStore",
    "evaluate_governance",
]
