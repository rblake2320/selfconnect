from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from experiments.fabric_v2 import real_agent_baseline as baseline


@contextmanager
def local_tmpdir() -> Iterator[Path]:
    root = Path("tests") / "_tmp"
    root.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="real_agent_baseline_", dir=root))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_provider_plan_defaults_to_codex() -> None:
    assert baseline._provider_plan(None, 3) == ["codex", "codex", "codex"]


def test_provider_plan_expands_counted_spec() -> None:
    assert baseline._provider_plan("codex:2,claude:1", 3) == [
        "codex",
        "codex",
        "claude",
    ]


def test_provider_plan_round_robins_unweighted_spec() -> None:
    assert baseline._provider_plan("codex,claude", 5) == [
        "codex",
        "claude",
        "codex",
        "claude",
        "codex",
    ]


def test_provider_plan_rejects_count_mismatch() -> None:
    with pytest.raises(ValueError, match="provider counts sum"):
        baseline._provider_plan("codex:1,claude:1", 3)


def test_provider_plan_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        baseline._provider_plan("codex:1,bad:1", 2)


def test_baseline_file_preserves_codex_legacy_name() -> None:
    assert baseline._baseline_file_name(["codex"] * 5, 5) == "baseline_5agent_real.json"


def test_baseline_file_uses_provider_key_for_mixed_runs() -> None:
    assert (
        baseline._baseline_file_name(["codex", "claude", "codex", "claude", "codex"], 5)
        == "baseline_5agent_real_claude2_codex3.json"
    )


def test_title_match_requires_role_boundary() -> None:
    assert baseline._title_matches("SC_RUN realclaude-1", "SC_RUN", "realclaude-1")
    assert baseline._title_matches("SC_RUN realclaude-1 DONE", "SC_RUN", "realclaude-1")
    assert not baseline._title_matches("SC_RUN realclaude-10", "SC_RUN", "realclaude-1")
    assert not baseline._title_matches("SC_RUN realclaude-10 DONE", "SC_RUN", "realclaude-1")


def test_has_exact_line_rejects_substring_ack() -> None:
    expected = "ACK_PREFLIGHT provider=claude nonce=N"
    assert baseline._has_exact_line(expected, expected)
    assert baseline._has_exact_line(f"noise\n{expected}\nmore noise", expected)
    assert not baseline._has_exact_line(f"{expected} status=ready", expected)
    assert not baseline._has_exact_line(f"prompt says: {expected}", expected)


def test_write_agent_script_uses_claude_command() -> None:
    with local_tmpdir() as tmpdir:
        log = tmpdir / "claude.log"
        script = baseline._write_agent_script(
            workdir=tmpdir,
            run_id="RUN_TEST",
            provider="claude",
            role="realclaude-1",
            nonce="NONCE",
            expected="ACK_REAL_VENDOR provider=claude role=realclaude-1 nonce=NONCE",
            log=log,
            keep_open=False,
        )

        text = script.read_text(encoding="utf-8")
    assert "claude -p --permission-mode bypassPermissions" in text
    assert "SELFCONNECT_REAL_AGENT_START provider=claude role=realclaude-1" in text
    assert "ACK_REAL_VENDOR provider=claude role=realclaude-1 nonce=NONCE" in text
    assert "Do not change the provider, role, nonce" in text


def test_write_agent_script_makes_gemini_fail_fast() -> None:
    with local_tmpdir() as tmpdir:
        log = tmpdir / "gemini.log"
        script = baseline._write_agent_script(
            workdir=tmpdir,
            run_id="RUN_TEST",
            provider="gemini",
            role="realgemini-1",
            nonce="NONCE",
            expected="ACK_REAL_VENDOR provider=gemini role=realgemini-1 nonce=NONCE",
            log=log,
            keep_open=False,
        )

        text = script.read_text(encoding="utf-8")
    assert "$env:CI = 'true'" in text
    assert "gemini -p $prompt --approval-mode yolo" in text
    assert "GetEnvironmentVariable('GEMINI_API_KEY', 'User')" in text
    assert "GetEnvironmentVariable('GEMINI_API_KEY', 'Machine')" in text
    assert "SetEnvironmentVariable('GEMINI_API_KEY', $value, 'Process')" in text


def test_provider_preflight_script_loads_gemini_auth_from_os_env() -> None:
    with local_tmpdir() as tmpdir:
        log = tmpdir / "gemini.log"
        script = baseline._write_provider_preflight_script(
            workdir=tmpdir,
            provider="gemini",
            expected="ACK_PREFLIGHT provider=gemini nonce=N",
            log=log,
        )

        text = script.read_text(encoding="utf-8")

    assert "GetEnvironmentVariable('GEMINI_API_KEY', 'User')" in text
    assert "GetEnvironmentVariable('GOOGLE_APPLICATION_CREDENTIALS', 'User')" in text
    assert "GetEnvironmentVariable('GOOGLE_CLOUD_PROJECT', 'User')" in text
    assert "GetEnvironmentVariable('CLOUDSDK_CONFIG', 'User')" in text
    assert "ACK_PREFLIGHT provider=gemini nonce=N" in text


def test_temporary_gemini_auth_type_restores_existing_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    with local_tmpdir() as tmpdir:
        settings = tmpdir / "settings.json"
        original = b'{"security":{"auth":{"selectedType":"oauth-personal"}},"keep":true}\n'
        settings.write_bytes(original)
        monkeypatch.setattr(baseline, "_gemini_settings_path", lambda: settings)

        with baseline._temporary_gemini_auth_type("gemini-api-key"):
            data = json.loads(settings.read_text(encoding="utf-8"))
            assert data["security"]["auth"]["selectedType"] == "gemini-api-key"
            assert data["keep"] is True

        assert settings.read_bytes() == original


def test_temporary_gemini_auth_type_removes_created_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    with local_tmpdir() as tmpdir:
        settings = tmpdir / "nested" / "settings.json"
        monkeypatch.setattr(baseline, "_gemini_settings_path", lambda: settings)

        with baseline._temporary_gemini_auth_type("gemini-api-key"):
            data = json.loads(settings.read_text(encoding="utf-8"))
            assert data["security"]["auth"]["selectedType"] == "gemini-api-key"

        assert not settings.exists()


def test_cleanup_run_command_targets_run_workspace() -> None:
    command = baseline._cleanup_run_command("SC_REAL5_20260621_070344")

    assert "selfconnect_real_agent_baseline_$run" in command
    assert "Stop-Process" in command
    assert "powershell.exe" in command
    assert "SC_REAL5_20260621_070344" in command


def test_cleanup_run_rejects_non_real5_id() -> None:
    with pytest.raises(ValueError, match="must start"):
        baseline.cleanup_run("not-a-run")


def test_write_run_state_records_progress_without_raw_nonce() -> None:
    with local_tmpdir() as tmpdir:
        agent = baseline.AgentRun(
            provider="gemini",
            role="realgemini-1",
            nonce="SECRET_NONCE",
            expected="ACK_REAL_VENDOR provider=gemini role=realgemini-1 nonce=SECRET_NONCE",
            script=tmpdir / "a.ps1",
            log=tmpdir / "a.log",
            status="pass",
            ack_ms=123.0,
        )

        baseline._write_run_state(
            results_dir=tmpdir,
            run_id="SC_REAL5_TEST",
            phase="polling",
            started=0.0,
            agents=[agent],
            processes=[SimpleNamespace(pid=1234)],  # type: ignore[list-item]
            pending=set(),
            provider_plan=["gemini"],
            gemini_auth_type="gemini-api-key",
        )

        text = (tmpdir / "real_agent_state_SC_REAL5_TEST.json").read_text(
            encoding="utf-8"
        )
        state = json.loads(text)

    assert state["phase"] == "polling"
    assert state["pass_count"] == 1
    assert state["process_pids"] == [1234]
    assert state["agents"][0]["expected_hash"]
    assert "SECRET_NONCE" not in text


def test_log_has_exact_ack_accepts_only_standalone_line() -> None:
    with local_tmpdir() as tmpdir:
        log = tmpdir / "agent.log"
        agent = baseline.AgentRun(
            provider="gemini",
            role="realgemini-1",
            nonce="NONCE",
            expected="ACK_REAL_VENDOR provider=gemini role=realgemini-1 nonce=NONCE",
            script=tmpdir / "agent.ps1",
            log=log,
        )

        log.write_text(
            "prompt echoed ACK_REAL_VENDOR provider=gemini role=realgemini-1 nonce=NONCE extra\n",
            encoding="utf-8",
        )
        assert not baseline._log_has_exact_ack(agent)

        log.write_text(
            "noise\nACK_REAL_VENDOR provider=gemini role=realgemini-1 nonce=NONCE\n",
            encoding="utf-8",
        )
        assert baseline._log_has_exact_ack(agent)


def test_diagnose_failed_agent_wrong_ack_format() -> None:
    with local_tmpdir() as tmpdir:
        log = tmpdir / "agent.log"
        log.write_text(
            "ACK_REAL_VENDOR provider=claude role=realclaude-orchestrator "
            "nonce=NONCE status=ready\n",
            encoding="utf-8",
        )
        agent = baseline.AgentRun(
            provider="claude",
            role="realclaude-7",
            nonce="NONCE",
            expected="ACK_REAL_VENDOR provider=claude role=realclaude-7 nonce=NONCE",
            script=tmpdir / "agent.ps1",
            log=log,
            status="fail",
            error="expected ACK not observed via UIA before timeout",
        )

        baseline._diagnose_failed_agent(agent)

    assert agent.diagnosis == "wrong_ack_format"
    assert "exact expected ACK" in agent.error


def test_diagnose_failed_agent_auth_required() -> None:
    with local_tmpdir() as tmpdir:
        log = tmpdir / "agent.log"
        log.write_text("FatalAuthenticationError: Manual authorization is required\n", encoding="utf-8")
        agent = baseline.AgentRun(
            provider="gemini",
            role="realgemini-1",
            nonce="NONCE",
            expected="ACK_REAL_VENDOR provider=gemini role=realgemini-1 nonce=NONCE",
            script=tmpdir / "agent.ps1",
            log=log,
            status="fail",
            error="expected ACK not observed via UIA before timeout",
        )

        baseline._diagnose_failed_agent(agent)

    assert agent.diagnosis == "provider_auth_required"
    assert "authentication required" in agent.error


def test_provider_output_ready() -> None:
    status, error = baseline._classify_provider_output(
        output="ACK_PREFLIGHT provider=codex nonce=N",
        expected="ACK_PREFLIGHT provider=codex nonce=N",
        nonce="N",
        returncode=0,
        timed_out=False,
    )

    assert status == "ready"
    assert error == ""


def test_provider_output_auth_required() -> None:
    status, error = baseline._classify_provider_output(
        output="FatalAuthenticationError: Manual authorization is required",
        expected="ACK_PREFLIGHT provider=gemini nonce=N",
        nonce="N",
        returncode=41,
        timed_out=False,
    )

    assert status == "provider_auth_required"
    assert "authentication required" in error


def test_provider_output_wrong_ack_format() -> None:
    status, error = baseline._classify_provider_output(
        output="ACK_PREFLIGHT provider=claude nonce=N status=ready",
        expected="ACK_PREFLIGHT provider=claude nonce=N",
        nonce="N",
        returncode=0,
        timed_out=False,
    )

    assert status == "wrong_ack_format"
    assert "exact expected ACK" in error


def test_provider_output_timeout() -> None:
    status, error = baseline._classify_provider_output(
        output="",
        expected="ACK_PREFLIGHT provider=gemini nonce=N",
        nonce="N",
        returncode=None,
        timed_out=True,
    )

    assert status == "timeout"
    assert "timed out" in error


def test_write_provider_preflight_script_uses_provider_command() -> None:
    with local_tmpdir() as tmpdir:
        log = tmpdir / "codex.log"
        script = baseline._write_provider_preflight_script(
            workdir=tmpdir,
            provider="codex",
            expected="ACK_PREFLIGHT provider=codex nonce=N",
            log=log,
        )

        text = script.read_text(encoding="utf-8")

    assert "codex exec --dangerously-bypass-approvals-and-sandbox" in text
    assert "ACK_PREFLIGHT provider=codex nonce=N" in text
