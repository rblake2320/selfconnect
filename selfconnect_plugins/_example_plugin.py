"""Example plugin showing the PLUGIN_CONTRACT convention."""

import sys
from pathlib import Path

# Ensure plugin_registry is importable from the parent SDK directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugin_registry import PluginContract

PLUGIN_CONTRACT = PluginContract(
    name="example",
    version="0.1.0",
    sdk_min_version="0.9.0",
    required_exports=["hello"],
)


def hello(name: str) -> str:
    """Example required export."""
    return f"Hello, {name}!"
