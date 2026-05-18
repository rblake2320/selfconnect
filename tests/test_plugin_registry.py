"""Tests for plugin_registry module."""

import sys
import textwrap
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugin_registry import PluginContract, PluginLoadError, PluginRegistry


def _write_plugin(tmp_path: Path, filename: str, code: str) -> Path:
    p = tmp_path / filename
    p.write_text(textwrap.dedent(code), encoding="utf-8")
    return p


@pytest.fixture
def valid_plugin_dir(tmp_path):
    _write_plugin(tmp_path, "good.py", """\
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
        from plugin_registry import PluginContract

        PLUGIN_CONTRACT = PluginContract(
            name="good",
            version="1.0.0",
            sdk_min_version="0.9.0",
            required_exports=["do_thing"],
        )

        def do_thing():
            return 42
    """)
    return tmp_path


class TestPluginRegistry:
    def test_valid_plugin_loads(self, valid_plugin_dir):
        reg = PluginRegistry(valid_plugin_dir)
        loaded, errors = reg.load_all()
        assert "good" in loaded
        assert errors == []

    def test_bad_semver_rejected(self, tmp_path):
        _write_plugin(tmp_path, "bad_ver.py", f"""\
            import sys, os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
            from plugin_registry import PluginContract

            PLUGIN_CONTRACT = PluginContract(
                name="bad_ver",
                version="not.a.version",
                sdk_min_version="0.9.0",
                required_exports=[],
            )
        """)
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert "bad_ver" not in loaded
        assert len(errors) == 1
        assert "version" in errors[0].reason.lower() or "semver" in errors[0].reason.lower()

    def test_missing_plugin_contract_rejected(self, tmp_path):
        _write_plugin(tmp_path, "no_contract.py", """\
            x = 1
        """)
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert loaded == []
        assert len(errors) == 1
        assert "PLUGIN_CONTRACT" in errors[0].reason

    def test_missing_required_export_rejected(self, tmp_path):
        _write_plugin(tmp_path, "missing_export.py", f"""\
            import sys, os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
            from plugin_registry import PluginContract

            PLUGIN_CONTRACT = PluginContract(
                name="missing_export",
                version="1.0.0",
                sdk_min_version="0.9.0",
                required_exports=["nonexistent_func"],
            )
        """)
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert loaded == []
        assert "nonexistent_func" in errors[0].reason

    def test_sdk_version_too_high_rejected(self, tmp_path):
        _write_plugin(tmp_path, "future.py", f"""\
            import sys, os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
            from plugin_registry import PluginContract

            PLUGIN_CONTRACT = PluginContract(
                name="future",
                version="1.0.0",
                sdk_min_version="99.0.0",
                required_exports=[],
            )
        """)
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert loaded == []
        assert "99.0.0" in errors[0].reason

    def test_empty_dir_returns_empty(self, tmp_path):
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert loaded == []
        assert errors == []

    def test_mixed_dir_returns_both(self, tmp_path):
        _write_plugin(tmp_path, "ok.py", f"""\
            import sys, os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
            from plugin_registry import PluginContract

            PLUGIN_CONTRACT = PluginContract(
                name="ok",
                version="1.0.0",
                sdk_min_version="0.9.0",
                required_exports=["greet"],
            )

            def greet():
                return "hi"
        """)
        _write_plugin(tmp_path, "broken.py", """\
            x = "no contract here"
        """)
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert "ok" in loaded
        assert len(errors) == 1

    def test_get_returns_module(self, valid_plugin_dir):
        reg = PluginRegistry(valid_plugin_dir)
        reg.load_all()
        mod = reg.get("good")
        assert mod is not None
        assert mod.do_thing() == 42
        assert reg.get("nonexistent") is None

    def test_syntax_error_plugin_rejected(self, tmp_path):
        """Plugin with a syntax error must not crash load_all — it becomes a PluginLoadError."""
        bad = tmp_path / "syntax_bad.py"
        bad.write_text("def broken(\n    # unterminated function — syntax error\n", encoding="utf-8")
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert loaded == []
        assert len(errors) == 1
        assert "syntax_bad" == errors[0].plugin_name
        # The error reason must mention import or syntax
        reason_lower = errors[0].reason.lower()
        assert "import" in reason_lower or "syntax" in reason_lower or "failed" in reason_lower

    def test_concurrent_load_all_is_safe(self, tmp_path):
        """Calling load_all() from multiple threads must not corrupt internal state."""
        _write_plugin(tmp_path, "concurrent.py", f"""\
            import sys, os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
            from plugin_registry import PluginContract

            PLUGIN_CONTRACT = PluginContract(
                name="concurrent",
                version="1.0.0",
                sdk_min_version="0.9.0",
                required_exports=["work"],
            )

            def work():
                return "done"
        """)

        results = []
        errors_collected = []

        def run_load():
            reg = PluginRegistry(tmp_path)
            loaded, errs = reg.load_all()
            results.append(loaded)
            errors_collected.extend(errs)

        threads = [threading.Thread(target=run_load) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Every thread must have loaded the plugin successfully
        assert len(results) == 10
        for r in results:
            assert "concurrent" in r
        assert errors_collected == []

    def test_contracts_returns_copy(self, valid_plugin_dir):
        """contracts() must return a dict copy — mutations must not affect registry state."""
        reg = PluginRegistry(valid_plugin_dir)
        reg.load_all()
        c = reg.contracts()
        assert "good" in c
        # Mutate the copy
        del c["good"]
        # Registry should still hold the contract
        assert "good" in reg.contracts()

    def test_non_py_files_ignored(self, tmp_path):
        """Non-.py files in the plugin directory must not produce errors."""
        (tmp_path / "readme.txt").write_text("not a plugin", encoding="utf-8")
        (tmp_path / "data.json").write_text("{}", encoding="utf-8")
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert loaded == []
        assert errors == []

    def test_private_files_ignored(self, tmp_path):
        """Files starting with '_' must be skipped (e.g. _example_plugin.py)."""
        (tmp_path / "_internal.py").write_text("x = 1", encoding="utf-8")
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert loaded == []
        assert errors == []
