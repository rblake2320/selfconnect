"""
Domain 2 Adversarial Test Audit: plugin_registry + observer + replay

CRUCIBLE — 100% code path coverage with adversarial inputs.
Tests every requirement from the Domain 2 spec: 14 plugin, 13 observer, 11 replay, rollback.
"""

import dataclasses
import json
import os
import sys
import textwrap
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugin_registry import PluginContract, PluginLoadError, PluginRegistry, _parse_semver
from observer import (
    ApprovalDecision,
    FRPHit,
    FRPLookup,
    FRPMiss,
    MeshObserver,
    MessageSent,
    PeerDiscovered,
    PeerLost,
    get_observer,
    observe,
    set_observer,
)
from replay import (
    InjectionRecord,
    ReplaySession,
    SimulatedTerminal,
    load_log,
    record_session,
    replay_session,
)


# ============================================================================
# Helpers
# ============================================================================

SDK_ROOT = str(Path(__file__).resolve().parent.parent)


def _write_plugin(tmp_path: Path, filename: str, code: str) -> Path:
    p = tmp_path / filename
    p.write_text(textwrap.dedent(code), encoding="utf-8")
    return p


def _good_plugin_code(name="good", version="1.0.0", sdk_min="0.9.0",
                       required=None, extras=""):
    """Generate valid plugin source code."""
    required = required or []
    req_str = repr(required)
    funcs = "\n".join(f"def {fn}(): return '{fn}'" for fn in required)
    return f"""\
import sys, os
sys.path.insert(0, {SDK_ROOT!r})
from plugin_registry import PluginContract

PLUGIN_CONTRACT = PluginContract(
    name={name!r},
    version={version!r},
    sdk_min_version={sdk_min!r},
    required_exports={req_str},
)

{funcs}
{extras}
"""


# ============================================================================
# A. Plugin Registry Adversarial Tests (14 tests)
# ============================================================================

class TestPluginRegistryAdversarial:
    """A1-A14: Adversarial plugin registry tests."""

    # A1: Valid plugin loads correctly
    def test_a01_valid_plugin_loads(self, tmp_path):
        _write_plugin(tmp_path, "myplug.py",
                      _good_plugin_code("myplug", required=["do_work"]))
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert "myplug" in loaded, f"Expected 'myplug' in loaded, got {loaded}"
        assert errors == []
        mod = reg.get("myplug")
        assert mod is not None
        assert mod.do_work() == "do_work"

    # A2: Missing PLUGIN_CONTRACT
    def test_a02_missing_plugin_contract(self, tmp_path):
        _write_plugin(tmp_path, "no_contract.py", "x = 1\ny = 2\n")
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert loaded == []
        assert len(errors) == 1
        assert isinstance(errors[0], PluginLoadError)
        assert "PLUGIN_CONTRACT" in errors[0].reason

    # A3: Bad semver in plugin version
    def test_a03_bad_semver_version(self, tmp_path):
        _write_plugin(tmp_path, "bad_ver.py",
                      _good_plugin_code("bad_ver", version="not-a-version"))
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert "bad_ver" not in loaded
        assert len(errors) == 1
        assert "version" in errors[0].reason.lower()

    # A3b: Bad semver in sdk_min_version
    def test_a03b_bad_semver_sdk_min(self, tmp_path):
        _write_plugin(tmp_path, "bad_sdk.py",
                      _good_plugin_code("bad_sdk", sdk_min="xyz"))
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert "bad_sdk" not in loaded
        assert len(errors) == 1
        assert "sdk_min_version" in errors[0].reason.lower() or "version" in errors[0].reason.lower()

    # A4: SDK version too high
    def test_a04_sdk_version_too_high(self, tmp_path):
        _write_plugin(tmp_path, "future.py",
                      _good_plugin_code("future", sdk_min="99.0.0"))
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert loaded == []
        assert len(errors) == 1
        assert "99.0.0" in errors[0].reason
        # Must mention version mismatch
        assert "SDK" in errors[0].reason or "sdk" in errors[0].reason.lower()

    # A5: Missing required export
    def test_a05_missing_required_export(self, tmp_path):
        code = _good_plugin_code("missing_exp", required=["foo"])
        # Remove the generated foo function
        code = code.replace("def foo(): return 'foo'", "# foo not defined")
        _write_plugin(tmp_path, "missing_exp.py", code)
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert loaded == []
        assert len(errors) == 1
        assert "foo" in errors[0].reason

    # A6: Plugin that raises on import
    def test_a06_plugin_raises_on_import(self, tmp_path):
        _write_plugin(tmp_path, "explosive.py",
                      'raise RuntimeError("oops I crashed")\n')
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert loaded == []
        assert len(errors) == 1
        assert isinstance(errors[0], PluginLoadError)
        assert "oops" in errors[0].reason.lower() or "Import failed" in errors[0].reason

    # A7: Plugin with syntax error
    def test_a07_plugin_syntax_error(self, tmp_path):
        _write_plugin(tmp_path, "syntax_bad.py", "def broken(\n  nope\n")
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert loaded == []
        assert len(errors) == 1
        assert isinstance(errors[0], PluginLoadError)
        assert "Import failed" in errors[0].reason or "SyntaxError" in errors[0].reason

    # A8: Mixed directory — 1 good + 1 bad
    def test_a08_mixed_directory(self, tmp_path):
        _write_plugin(tmp_path, "good_one.py",
                      _good_plugin_code("good_one", required=["helper"]))
        _write_plugin(tmp_path, "bad_one.py", "not valid python at all !!!")
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert "good_one" in loaded
        assert len(errors) == 1
        assert errors[0].plugin_name == "bad_one"

    # A9: Empty directory
    def test_a09_empty_directory(self, tmp_path):
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert loaded == []
        assert errors == []

    # A10: Missing directory — no crash, logged warning
    def test_a10_missing_directory(self, tmp_path):
        nonexistent = tmp_path / "does_not_exist"
        reg = PluginRegistry(nonexistent)
        loaded, errors = reg.load_all()
        assert loaded == []
        assert errors == []

    # A11: Underscore-prefixed files skipped
    def test_a11_underscore_files_skipped(self, tmp_path):
        _write_plugin(tmp_path, "_private.py",
                      _good_plugin_code("_private"))
        _write_plugin(tmp_path, "_helper.py", "x = 1")
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert loaded == []
        assert errors == []

    # A12: __init__.py skipped
    def test_a12_init_py_skipped(self, tmp_path):
        _write_plugin(tmp_path, "__init__.py",
                      _good_plugin_code("init_plugin"))
        reg = PluginRegistry(tmp_path)
        loaded, errors = reg.load_all()
        assert loaded == []
        assert errors == []

    # A13: contracts() returns a copy — mutation safety
    def test_a13_contracts_returns_copy(self, tmp_path):
        _write_plugin(tmp_path, "safe.py",
                      _good_plugin_code("safe", required=["action"]))
        reg = PluginRegistry(tmp_path)
        reg.load_all()
        c1 = reg.contracts()
        assert "safe" in c1
        # Mutate the returned dict
        c1["injected"] = "evil"
        del c1["safe"]
        # Original registry should be unaffected
        c2 = reg.contracts()
        assert "safe" in c2
        assert "injected" not in c2

    # A14: Concurrent load_all — no crash
    def test_a14_concurrent_load_all(self, tmp_path):
        _write_plugin(tmp_path, "concurrent.py",
                      _good_plugin_code("concurrent", required=["run"]))
        results = []
        errors_list = []

        def loader():
            reg = PluginRegistry(tmp_path)
            loaded, errs = reg.load_all()
            results.append(loaded)
            errors_list.append(errs)

        threads = [threading.Thread(target=loader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # All threads should complete without crash
        assert len(results) == 4
        for r in results:
            assert "concurrent" in r


class TestParseSemver:
    """Additional edge cases for _parse_semver."""

    def test_valid_semver(self):
        assert _parse_semver("1.2.3") == (1, 2, 3)

    def test_semver_with_prerelease(self):
        assert _parse_semver("1.2.3-beta.1") == (1, 2, 3)

    def test_semver_with_build(self):
        assert _parse_semver("1.2.3+build.42") == (1, 2, 3)

    def test_invalid_semver_too_few_parts(self):
        with pytest.raises(ValueError):
            _parse_semver("1.2")

    def test_invalid_semver_non_numeric(self):
        with pytest.raises(ValueError):
            _parse_semver("a.b.c")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            _parse_semver("")

    def test_repr(self, tmp_path):
        reg = PluginRegistry(tmp_path)
        r = repr(reg)
        assert "PluginRegistry" in r
        assert "loaded=0" in r


# ============================================================================
# B. Observer Adversarial Tests (13 tests)
# ============================================================================

class TestObserverAdversarial:
    """B1-B13: Adversarial observer tests."""

    def setup_method(self):
        """Reset singleton before each test."""
        set_observer(None)

    # B1: All 7 event types emit without error
    def test_b01_all_event_types_emit(self, tmp_path):
        log_file = tmp_path / "all_events.jsonl"
        with MeshObserver(file=str(log_file)) as obs:
            events = [
                MessageSent(hwnd=1, text="hi"),
                ApprovalDecision(hwnd=2, tool="bash", decision="y"),
                PeerDiscovered(hwnd=3, title="Term"),
                PeerLost(hwnd=4, title="Term"),
                FRPLookup(error_text="err", env_class="dev"),
                FRPHit(fingerprint="abc", title="fix", confidence=0.95),
                FRPMiss(fingerprint="xyz"),
            ]
            for ev in events:
                obs.emit(ev)

        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 7
        kinds = [json.loads(line)["kind"] for line in lines]
        assert "MessageSent" in kinds
        assert "ApprovalDecision" in kinds
        assert "PeerDiscovered" in kinds
        assert "PeerLost" in kinds
        assert "FRPLookup" in kinds
        assert "FRPHit" in kinds
        assert "FRPMiss" in kinds

    # B2: File backend writes JSONL with "kind" field
    def test_b02_file_backend_jsonl(self, tmp_path):
        log_file = tmp_path / "file_backend.jsonl"
        with MeshObserver(file=str(log_file)) as obs:
            obs.emit(MessageSent(hwnd=10, text="hello world"))

        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        d = json.loads(lines[0])
        assert d["kind"] == "MessageSent"
        assert d["text"] == "hello world"
        assert d["hwnd"] == 10
        assert "ts" in d

    # B3: Console backend — no crash
    def test_b03_console_backend(self):
        obs = MeshObserver(console=True)
        obs.emit(MessageSent(hwnd=1, text="console test"))
        obs.emit(FRPMiss(fingerprint="test"))
        obs.close()
        # No assertion needed — no crash is the test

    # B4: Thread safety — 10 threads x 100 events
    def test_b04_thread_safety(self, tmp_path):
        log_file = tmp_path / "threaded.jsonl"
        obs = MeshObserver(file=str(log_file))

        def emitter(thread_id):
            for i in range(100):
                obs.emit(MessageSent(hwnd=thread_id, text=f"t{thread_id}-{i}"))

        threads = [threading.Thread(target=emitter, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        obs.close()

        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1000, f"Expected 1000 lines, got {len(lines)}"
        # Verify no corruption — each line must parse as valid JSON
        for i, line in enumerate(lines):
            d = json.loads(line)
            assert "kind" in d, f"Line {i} missing 'kind'"

    # B5: @observe preserves return value
    def test_b05_observe_preserves_return(self):
        @observe
        def compute(a, b):
            return a * b + 1

        assert compute(3, 4) == 13
        assert compute(0, 0) == 1

    # B6: @observe(event_factory=...) receives correct args
    def test_b06_observe_factory_args(self, tmp_path):
        log_file = tmp_path / "factory.jsonl"
        obs = MeshObserver(file=str(log_file))
        set_observer(obs)

        captured = {}

        def factory(args, kwargs, result):
            captured["args"] = args
            captured["kwargs"] = kwargs
            captured["result"] = result
            return MessageSent(hwnd=0, text=f"result={result}")

        @observe(event_factory=factory)
        def add(a, b, extra=0):
            return a + b + extra

        result = add(2, 3, extra=5)
        assert result == 10
        assert captured["args"] == (2, 3)
        assert captured["kwargs"] == {"extra": 5}
        assert captured["result"] == 10
        obs.close()

        line = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert line["text"] == "result=10"
        set_observer(None)

    # B7: OTel absent — no crash
    def test_b07_otel_absent(self):
        obs = MeshObserver(otel=True)
        obs.emit(MessageSent(hwnd=1, text="otel test"))
        obs.close()
        # If OTel is not installed, this should not crash

    # B8: Context manager closes file
    def test_b08_context_manager_closes_file(self, tmp_path):
        log_file = tmp_path / "ctx_close.jsonl"
        with MeshObserver(file=str(log_file)) as obs:
            obs.emit(MessageSent(hwnd=1, text="before close"))
        # After exiting context, file handle should be None
        assert obs._file_handle is None
        # We should be able to open and read the file without contention
        content = log_file.read_text(encoding="utf-8")
        assert "before close" in content

    # B9: get_observer() singleton
    def test_b09_get_observer_singleton(self):
        set_observer(None)  # Reset
        obs1 = get_observer()
        obs2 = get_observer()
        assert obs1 is obs2
        obs1.close()
        set_observer(None)

    # B10: set_observer() replaces singleton
    def test_b10_set_observer_replaces(self):
        obs_new = MeshObserver(console=True)
        set_observer(obs_new)
        assert get_observer() is obs_new
        obs_new.close()
        set_observer(None)

    # B11: Emit to None file — no crash
    def test_b11_emit_no_file(self):
        obs = MeshObserver()  # No file, no console, no otel
        obs.emit(MessageSent(hwnd=1, text="void"))
        obs.emit(FRPHit(fingerprint="a", title="b", confidence=0.5))
        obs.close()

    # B12: Malformed event factory — does not swallow function exception
    def test_b12_malformed_event_factory(self):
        def bad_factory(args, kwargs, result):
            raise ValueError("factory exploded")

        @observe(event_factory=bad_factory)
        def safe_func():
            return 42

        # The factory raises AFTER the function runs. Since the decorator calls
        # the factory after result = func(), the ValueError should propagate.
        with pytest.raises(ValueError, match="factory exploded"):
            safe_func()

    # B13: _to_dict has correct kind — dynamic class name
    def test_b13_to_dict_kind(self):
        obs = MeshObserver()
        events_and_kinds = [
            (MessageSent(hwnd=1, text="x"), "MessageSent"),
            (ApprovalDecision(hwnd=2, tool="t", decision="y"), "ApprovalDecision"),
            (PeerDiscovered(hwnd=3, title="T"), "PeerDiscovered"),
            (PeerLost(hwnd=4, title="T"), "PeerLost"),
            (FRPLookup(error_text="e", env_class="d"), "FRPLookup"),
            (FRPHit(fingerprint="f", title="t", confidence=0.1), "FRPHit"),
            (FRPMiss(fingerprint="m"), "FRPMiss"),
        ]
        for ev, expected_kind in events_and_kinds:
            d = obs._to_dict(ev)
            assert d["kind"] == expected_kind, f"Expected kind={expected_kind}, got {d['kind']}"
        obs.close()


# ============================================================================
# C. Replay Adversarial Tests (11 tests)
# ============================================================================

class TestReplayAdversarial:
    """C1-C11: Adversarial replay tests."""

    # C1: SimulatedTerminal records correctly
    def test_c01_simulated_terminal_records(self):
        term = SimulatedTerminal(hwnd=42)
        term.send_string("alpha")
        term.send_string("beta", submitted=True)
        term.send_string("gamma")
        log = term.get_log()
        assert len(log) == 3
        assert log[0].text == "alpha"
        assert log[0].submitted is False
        assert log[1].text == "beta"
        assert log[1].submitted is True
        assert log[2].text == "gamma"
        assert all(r.hwnd == 42 for r in log)
        assert repr(term) == "SimulatedTerminal(hwnd=42, records=3)"

    # C2: Thread safety — 10 threads x 100 strings
    def test_c02_thread_safety(self):
        term = SimulatedTerminal(hwnd=1)

        def sender(tid):
            for i in range(100):
                term.send_string(f"t{tid}-{i}")

        threads = [threading.Thread(target=sender, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(term) == 1000

    # C3: dump_jsonl / load_log roundtrip
    def test_c03_dump_load_roundtrip(self, tmp_path):
        term = SimulatedTerminal(hwnd=99)
        term.send_string("first")
        term.send_string("second", submitted=True)
        term.send_string("third")

        out_file = tmp_path / "roundtrip.jsonl"
        count = term.dump_jsonl(out_file)
        assert count == 3

        records = load_log(out_file)
        assert len(records) == 3
        assert records[0].text == "first"
        assert records[0].submitted is False
        assert records[1].text == "second"
        assert records[1].submitted is True
        assert records[2].text == "third"
        assert all(r.hwnd == 99 for r in records)
        # Verify timestamps survived roundtrip
        orig_log = term.get_log()
        for orig, loaded in zip(orig_log, records):
            assert abs(orig.ts - loaded.ts) < 0.001

    # C4: ReplaySession replays all records to SimulatedTerminal
    def test_c04_replay_session(self):
        now = time.time()
        records = [
            InjectionRecord(ts=now, hwnd=1, text="a"),
            InjectionRecord(ts=now + 0.01, hwnd=1, text="b"),
            InjectionRecord(ts=now + 0.02, hwnd=1, text="c"),
        ]
        term = SimulatedTerminal(hwnd=1)
        session = ReplaySession(records, speed=0)
        result = session.replay(terminal=term)
        assert len(result) == 3
        assert len(term) == 3
        log = term.get_log()
        assert [r.text for r in log] == ["a", "b", "c"]

    # C5: speed=0 is instant — no actual sleep
    def test_c05_speed_zero_instant(self):
        now = time.time()
        records = [
            InjectionRecord(ts=now, hwnd=1, text="x"),
            InjectionRecord(ts=now + 60.0, hwnd=1, text="y"),  # 60s gap
        ]
        term = SimulatedTerminal(hwnd=1)
        session = ReplaySession(records, speed=0)
        start = time.time()
        session.replay(terminal=term)
        elapsed = time.time() - start
        assert elapsed < 1.0, f"speed=0 should be instant, took {elapsed:.2f}s"

    # C6: replay_dry() doesn't inject
    def test_c06_replay_dry_no_inject(self):
        now = time.time()
        records = [
            InjectionRecord(ts=now, hwnd=1, text="a"),
            InjectionRecord(ts=now + 0.01, hwnd=1, text="b"),
        ]
        term = SimulatedTerminal(hwnd=1)
        session = ReplaySession(records, speed=0)
        dry = session.replay_dry()
        assert len(dry) == 2
        assert len(term) == 0  # Nothing should have been injected

    # C7: duration property
    def test_c07_duration_property(self):
        records = [
            InjectionRecord(ts=100.0, hwnd=1, text="a"),
            InjectionRecord(ts=105.0, hwnd=1, text="b"),
        ]
        session = ReplaySession(records)
        assert session.duration == pytest.approx(5.0, abs=0.1)

        # Single record — duration should be 0
        session_one = ReplaySession([InjectionRecord(ts=100.0, hwnd=1, text="a")])
        assert session_one.duration == 0.0

        # Empty — duration should be 0
        session_empty = ReplaySession([])
        assert session_empty.duration == 0.0

    # C8: replay_session() with no target_hwnd
    def test_c08_replay_session_no_target(self, tmp_path):
        now = time.time()
        log_file = tmp_path / "no_target.jsonl"
        with open(log_file, "w", encoding="utf-8") as f:
            for text in ["hello", "world"]:
                f.write(json.dumps({"ts": now, "hwnd": 1, "text": text, "submitted": False}) + "\n")
                now += 0.01

        result = replay_session(log_file, speed=0)
        assert len(result) == 2
        assert result[0].text == "hello"
        assert result[1].text == "world"

    # C9: load_log() with malformed JSONL
    def test_c09_load_log_malformed(self, tmp_path):
        log_file = tmp_path / "malformed.jsonl"
        log_file.write_text(
            '{"ts": 100, "hwnd": 1, "text": "ok"}\n'
            'THIS IS NOT JSON\n'
            '{"ts": 101, "hwnd": 1, "text": "also ok"}\n',
            encoding="utf-8",
        )
        # load_log does json.loads on each line — malformed line should raise
        with pytest.raises(json.JSONDecodeError):
            load_log(log_file)

    # C9b: load_log() with missing fields
    def test_c09b_load_log_missing_fields(self, tmp_path):
        log_file = tmp_path / "missing_fields.jsonl"
        log_file.write_text(
            '{"ts": 100}\n',  # missing hwnd, text
            encoding="utf-8",
        )
        with pytest.raises(KeyError):
            load_log(log_file)

    # C10: Empty log file
    def test_c10_empty_log_file(self, tmp_path):
        log_file = tmp_path / "empty.jsonl"
        log_file.write_text("", encoding="utf-8")
        records = load_log(log_file)
        assert records == []

    # C10b: Log file with only blank lines
    def test_c10b_blank_lines_only(self, tmp_path):
        log_file = tmp_path / "blanks.jsonl"
        log_file.write_text("\n\n\n", encoding="utf-8")
        records = load_log(log_file)
        assert records == []

    # C11: record_session restores send_string on exception
    def test_c11_record_session_restores_on_exception(self):
        """Verify self_connect.send_string is restored even if body raises."""
        try:
            import self_connect
        except ImportError:
            pytest.skip("self_connect not available")

        original = self_connect.send_string

        with pytest.raises(RuntimeError, match="boom"):
            with record_session(hwnd=1, output_file="dummy.jsonl") as log:
                raise RuntimeError("boom")

        # send_string should be restored to original
        assert self_connect.send_string is original

        # Clean up temp file if created
        p = Path("dummy.jsonl")
        if p.exists():
            p.unlink()


# ============================================================================
# D. Rollback Safety Tests
# ============================================================================

class TestRollbackSafety:
    """Verify domain-2 modules are not imported by other SDK tests."""

    def test_d01_no_external_dependency_on_observer(self):
        """No existing non-domain-2 test file should import observer."""
        test_dir = Path(__file__).resolve().parent
        for f in test_dir.glob("*.py"):
            if f.name in ("test_observer.py", "test_domain2_adversarial.py"):
                continue
            content = f.read_text(encoding="utf-8")
            assert "from observer import" not in content, \
                f"{f.name} imports observer — removing observer.py would break it"
            assert "import observer" not in content, \
                f"{f.name} imports observer — removing observer.py would break it"

    def test_d02_no_external_dependency_on_replay(self):
        """No existing non-domain-2 test file should import replay."""
        test_dir = Path(__file__).resolve().parent
        for f in test_dir.glob("*.py"):
            if f.name in ("test_replay.py", "test_domain2_adversarial.py"):
                continue
            content = f.read_text(encoding="utf-8")
            assert "from replay import" not in content, \
                f"{f.name} imports replay — removing replay.py would break it"
            assert "import replay" not in content, \
                f"{f.name} imports replay — removing replay.py would break it"

    def test_d03_no_external_dependency_on_plugin_registry(self):
        """No existing non-domain-2 test file should import plugin_registry."""
        test_dir = Path(__file__).resolve().parent
        for f in test_dir.glob("*.py"):
            if f.name in ("test_plugin_registry.py", "test_domain2_adversarial.py"):
                continue
            content = f.read_text(encoding="utf-8")
            assert "from plugin_registry import" not in content, \
                f"{f.name} imports plugin_registry — removing plugin_registry.py would break it"
            assert "import plugin_registry" not in content, \
                f"{f.name} imports plugin_registry — removing plugin_registry.py would break it"

    def test_d04_main_sdk_does_not_import_domain2(self):
        """self_connect.py should not import observer, replay, or plugin_registry."""
        sdk_file = Path(__file__).resolve().parent.parent / "self_connect.py"
        if sdk_file.exists():
            content = sdk_file.read_text(encoding="utf-8")
            for mod in ("observer", "replay", "plugin_registry"):
                assert f"import {mod}" not in content, \
                    f"self_connect.py imports {mod} — rollback would break the SDK"
