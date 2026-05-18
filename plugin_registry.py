"""
Versioned plugin contract registry for SelfConnect SDK.

Plugins export a module-level PLUGIN_CONTRACT: PluginContract dataclass.
The PluginRegistry loads plugins from a directory, validates contracts,
and rejects incompatible or malformed plugins with clear errors.

Usage:
    from plugin_registry import PluginRegistry, PluginContract

    registry = PluginRegistry("./selfconnect_plugins")
    loaded, errors = registry.load_all()
"""

from __future__ import annotations

import importlib.util
import logging
import pathlib
import sys
import types
from dataclasses import dataclass, field

__all__ = ["PluginContract", "PluginLoadError", "PluginRegistry"]

_log = logging.getLogger(__name__)


def _parse_semver(v: str) -> tuple[int, int, int]:
    """Parse a semver string into (major, minor, patch). Raises ValueError on failure."""
    parts = v.strip().split(".")
    if len(parts) < 3:
        raise ValueError(f"Invalid semver (need at least 3 parts): {v!r}")
    return (int(parts[0]), int(parts[1]), int(parts[2].split("-")[0].split("+")[0]))


# Read SDK version at import time; fall back to conservative value if unavailable
try:
    _cwd = pathlib.Path(__file__).parent
    if str(_cwd) not in sys.path:
        sys.path.insert(0, str(_cwd))
    import self_connect as _sc
    _SDK_VERSION: tuple[int, int, int] = _parse_semver(_sc.__version__)
except Exception:
    _SDK_VERSION = (0, 10, 0)


@dataclass
class PluginContract:
    """Describes a plugin's requirements and capabilities."""

    name: str
    version: str
    sdk_min_version: str
    required_exports: list[str]
    optional_exports: list[str] = field(default_factory=list)


class PluginLoadError(Exception):
    """Raised when a plugin fails to load or validate."""

    def __init__(self, plugin_name: str, reason: str) -> None:
        self.plugin_name = plugin_name
        self.reason = reason
        super().__init__(f"[{plugin_name}] {reason}")


class PluginRegistry:
    """Loads and manages versioned plugins from a directory."""

    def __init__(self, plugin_dir: str | pathlib.Path | None = None) -> None:
        if plugin_dir is None:
            self._dir = pathlib.Path.cwd() / "selfconnect_plugins"
        else:
            self._dir = pathlib.Path(plugin_dir)

        self._modules: dict[str, types.ModuleType] = {}
        self._contracts: dict[str, PluginContract] = {}

        if not self._dir.exists():
            _log.warning("Plugin directory does not exist: %s", self._dir)

    def load_all(self) -> tuple[list[str], list[PluginLoadError]]:
        """Load all valid plugins from the directory. Returns (loaded_names, errors)."""
        loaded: list[str] = []
        errors: list[PluginLoadError] = []

        if not self._dir.exists():
            return loaded, errors

        for path in sorted(self._dir.glob("*.py")):
            if path.name == "__init__.py" or path.name.startswith("_"):
                continue
            try:
                name = self._load_one(path)
                loaded.append(name)
            except PluginLoadError as e:
                errors.append(e)

        return loaded, errors

    def _load_one(self, path: pathlib.Path) -> str:
        """Load a single plugin file. Returns the plugin name. Raises PluginLoadError."""
        module_name = f"selfconnect_plugin_{path.stem}"
        plugin_label = path.stem

        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise PluginLoadError(plugin_label, "Could not create module spec")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except PluginLoadError:
            raise
        except Exception as e:
            raise PluginLoadError(plugin_label, f"Import failed: {e}") from e

        if not hasattr(module, "PLUGIN_CONTRACT"):
            raise PluginLoadError(plugin_label, "Missing PLUGIN_CONTRACT attribute")

        contract = module.PLUGIN_CONTRACT
        if not isinstance(contract, PluginContract):
            raise PluginLoadError(plugin_label, "PLUGIN_CONTRACT is not a PluginContract instance")

        self._validate(contract, module)

        self._modules[contract.name] = module
        self._contracts[contract.name] = contract
        _log.info("Loaded plugin: %s v%s", contract.name, contract.version)
        return contract.name

    def _validate(self, contract: PluginContract, module: types.ModuleType) -> None:
        """Validate a plugin contract against the current SDK. Raises PluginLoadError."""
        # Validate semver fields
        try:
            _parse_semver(contract.version)
        except ValueError as e:
            raise PluginLoadError(contract.name, f"Invalid plugin version: {e}") from e

        try:
            min_ver = _parse_semver(contract.sdk_min_version)
        except ValueError as e:
            raise PluginLoadError(contract.name, f"Invalid sdk_min_version: {e}") from e

        # Check SDK compatibility
        if min_ver > _SDK_VERSION:
            raise PluginLoadError(
                contract.name,
                f"Requires SDK >= {contract.sdk_min_version}, but current is "
                f"{'.'.join(str(x) for x in _SDK_VERSION)}",
            )

        # Check required exports
        for export in contract.required_exports:
            if not hasattr(module, export):
                raise PluginLoadError(
                    contract.name, f"Missing required export: {export!r}"
                )

    def get(self, name: str) -> types.ModuleType | None:
        """Get a loaded plugin module by name."""
        return self._modules.get(name)

    def contracts(self) -> dict[str, PluginContract]:
        """Return a copy of all loaded contracts."""
        return dict(self._contracts)

    def __repr__(self) -> str:
        return f"PluginRegistry(loaded={len(self._modules)}, dir={self._dir})"
