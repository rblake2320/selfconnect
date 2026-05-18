# SelfConnect Plugins

## PLUGIN_CONTRACT Convention

Every plugin in this directory must export a module-level `PLUGIN_CONTRACT` variable
that is an instance of `plugin_registry.PluginContract`.

## Requirements

1. File must be a `.py` file in this directory
2. Files starting with `_` or named `__init__.py` are skipped by the registry
3. Must contain: `PLUGIN_CONTRACT = PluginContract(...)`
4. Contract fields:
   - `name` — unique plugin identifier
   - `version` — semver string (e.g. "1.0.0")
   - `sdk_min_version` — minimum SelfConnect SDK version required
   - `required_exports` — list of function/attribute names the module MUST define
   - `optional_exports` — list of function/attribute names the module MAY define

## Validation

The registry checks:
- `version` and `sdk_min_version` are valid semver strings
- `sdk_min_version` is not higher than the running SDK version
- All names in `required_exports` exist as module attributes

Plugins that fail validation are rejected with a `PluginLoadError` — they do not
crash the registry or affect other plugins.

## Example

See `_example_plugin.py` (prefixed with `_` so the registry skips it — it is
documentation only).

## Loading Plugins

```python
from plugin_registry import PluginRegistry

registry = PluginRegistry("./selfconnect_plugins")
loaded, errors = registry.load_all()

for name in loaded:
    mod = registry.get(name)
    # Use the plugin...
```
