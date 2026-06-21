from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

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
