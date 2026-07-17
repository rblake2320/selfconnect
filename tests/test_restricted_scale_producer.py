from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from experiments.fabric_v2 import restricted_scale_producer as producer


def _source_env(**extra: str) -> dict[str, str]:
    source = {name: f"value-{name.lower()}" for name in producer.COMMON_ENV}
    source.update(extra)
    return source


def test_provider_env_never_leaks_gemini_key_to_other_providers() -> None:
    source = _source_env(
        OPENAI_API_KEY="openai",
        ANTHROPIC_API_KEY="anthropic",
        GEMINI_API_KEY="gemini",
        PYTHONPATH="unsafe",
    )
    assert "GEMINI_API_KEY" not in producer.provider_env("codex", source)
    assert "GEMINI_API_KEY" not in producer.provider_env("claude", source)
    assert set(producer.provider_env("gemini", source)) & {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
    } == {"GEMINI_API_KEY"}


def test_provider_env_rejects_missing_common_child_environment() -> None:
    with pytest.raises(producer.ProducerError, match="provider_common_environment_missing"):
        producer.provider_env("codex", {"PATH": "bin", "OPENAI_API_KEY": "secret"})


@pytest.mark.parametrize(
    "provider,token",
    [
        ("codex", "--dangerously-bypass-approvals-and-sandbox"),
        ("claude", "bypassPermissions"),
        ("gemini", "--skip-trust"),
        ("gemini", "yolo"),
    ],
)
def test_unsafe_provider_flags_are_rejected(provider: str, token: str) -> None:
    argv = producer.provider_argv(
        provider, "prompt", admin_policy=producer.Path("deny-all.toml")
    ) + [token]
    with pytest.raises(producer.ProducerError, match="unsafe_provider_flag"):
        producer.validate_restricted_argv(provider, argv)


def test_nonce_uses_32_cryptographic_random_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(producer.secrets, "token_bytes", lambda size: bytes(range(size)))
    assert producer.crypto_nonce() == bytes(range(32)).hex()
    monkeypatch.setattr(producer.secrets, "token_bytes", lambda _size: b"predictable-short")
    with pytest.raises(producer.ProducerError, match="nonce_source_invalid"):
        producer.crypto_nonce()


def test_nonce_does_not_derive_from_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    values = iter((b"a" * 32, b"b" * 32))
    monkeypatch.setattr(producer.time, "time", lambda: 1.0)
    monkeypatch.setattr(producer.secrets, "token_bytes", lambda _size: next(values))
    assert producer.crypto_nonce() != producer.crypto_nonce()


def test_provider_policy_digests_match_frozen_consumer_contract() -> None:
    pins = producer.provider_pins()
    assert set(pins["codex"]) == {
        "required_policy_projection_sha256",
        "required_tool_policy_sha256",
        "expected_cli_version",
        "expected_help_sha256",
        "expected_entrypoint_sha256",
        "expected_provider_exe_name",
    }
    assert pins["codex"]["required_policy_projection_sha256"] == (
        "008845ea35aa87cdf84f1f87d287213e877a4e7caac8cfa1899ab37d81716d7b"
    )
    assert pins["claude"]["required_policy_projection_sha256"] == (
        "2a0d78a6685bca211a77b2d06acdca5efe6bfe54f366e5500832c45dc9ae6f12"
    )
    assert pins["gemini"]["required_policy_projection_sha256"] == (
        "183a476458c2db6d5530e088ee742b28ec460cd5d35e9347eef7c1b1ef6965a2"
    )
    assert producer.sha256_bytes(producer.GEMINI_DENY_ALL_POLICY) == (
        producer.GEMINI_DENY_ALL_POLICY_SHA256
    )
    for provider, digest in producer.PROVIDER_ENTRYPOINT_SHA256.items():
        assert pins[provider]["expected_entrypoint_sha256"] == digest
        assert pins[provider]["expected_help_sha256"] == producer.PROVIDER_HELP_SHA256[provider]
        assert pins[provider]["expected_provider_exe_name"] == producer.PROVIDER_EXE_NAMES[provider]


def test_provider_pin_check_rejects_unpinned_help_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_env(
        OPENAI_API_KEY="openai",
        ANTHROPIC_API_KEY="anthropic",
        GEMINI_API_KEY="gemini",
    )
    monkeypatch.setattr(
        producer,
        "resolve_provider_runtime",
        lambda provider, _env: producer.ProviderRuntime(
            (provider,), producer.Path(f"{provider}.exe")
        ),
    )

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        provider = command[0]
        if command[1:] == ["--version"]:
            output = producer.PROVIDER_VERSIONS[provider]
        else:
            output = "help without required flags"
        return SimpleNamespace(returncode=0, stdout=output.encode())

    monkeypatch.setattr(producer.subprocess, "run", fake_run)
    with pytest.raises(producer.ProducerError, match="provider_help_digest_mismatch"):
        producer.verify_provider_pins(source)


def test_provider_home_isolation_rejects_ambient_gemini_config(tmp_path: producer.Path) -> None:
    (tmp_path / ".gemini").mkdir()
    with pytest.raises(producer.ProducerError, match="provider_home_not_disposable"):
        producer.verify_provider_home_isolation(
            {"USERPROFILE": str(tmp_path), "PROGRAMDATA": str(tmp_path / "programdata")}
        )


def test_provider_failure_markers_override_an_exact_ack() -> None:
    assert producer._provider_failure_observed(
        "ACK_REAL_VENDOR provider=gemini role=realgemini-1 nonce=N\nRESOURCE_EXHAUSTED"
    )


def test_observed_ack_requires_an_actual_standalone_line() -> None:
    expected = "ACK_REAL_VENDOR provider=codex role=realcodex-1 nonce=N"
    assert producer._observed_exact_line(f"noise\n{expected}\n", expected) == expected
    assert producer._observed_exact_line(f"prompt: {expected}", expected) is None


def _window(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "hwnd": 100,
        "pid": 200,
        "exe_name": producer.TERMINAL_EXE,
        "class_name": producer.TERMINAL_CLASS,
        "title": "SC_SCALE codex realcodex-1 " + "a" * 64,
    }
    row.update(changes)
    return row


def _tree(provider_exe: str = "codex.exe") -> list[dict[str, int | str | None]]:
    return [
        {"pid": 200, "parent_pid": None, "exe_name": producer.TERMINAL_EXE},
        {"pid": 250, "parent_pid": 200, "exe_name": "powershell.exe"},
        {"pid": 300, "parent_pid": 250, "exe_name": provider_exe},
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"hwnd": 101},
        {"pid": 201},
        {"exe_name": "powershell.exe"},
        {"class_name": "ConsoleWindowClass"},
        {"title": "wrong"},
    ],
)
def test_guard_rejects_wrong_hwnd_pid_exe_class_or_title(changes: dict[str, object]) -> None:
    with pytest.raises(producer.ProducerError, match="window_process_guard_failed"):
        producer.build_guard_receipt(
            provider="codex",
            role="realcodex-1",
            nonce="a" * 64,
            pre=_window(),
            post=_window(**changes),
            provider_pid=300,
            process_rows=_tree(),
            session_id=1,
            shell_session_id=1,
            provider_session_id=1,
            provider_entrypoint_sha256="0" * 64,
        )


def test_guard_rejects_provider_outside_spawn_tree() -> None:
    with pytest.raises(producer.ProducerError, match="window_process_guard_failed"):
        producer.build_guard_receipt(
            provider="codex",
            role="realcodex-1",
            nonce="a" * 64,
            pre=_window(),
            post=_window(),
            provider_pid=999,
            process_rows=_tree(),
            session_id=1,
            shell_session_id=1,
            provider_session_id=1,
            provider_entrypoint_sha256="0" * 64,
        )


def test_guard_rejects_cross_session_provider() -> None:
    with pytest.raises(producer.ProducerError, match="window_process_guard_failed"):
        producer.build_guard_receipt(
            provider="codex",
            role="realcodex-1",
            nonce="a" * 64,
            pre=_window(),
            post=_window(),
            provider_pid=300,
            process_rows=_tree(),
            session_id=1,
            shell_session_id=1,
            provider_session_id=2,
            provider_entrypoint_sha256="0" * 64,
        )


def test_guard_emits_recomputable_relational_process_projection() -> None:
    receipt = producer.build_guard_receipt(
        provider="codex",
        role="realcodex-1",
        nonce="a" * 64,
        pre=_window(),
        post=_window(),
        provider_pid=300,
        process_rows=_tree(),
        session_id=1,
        shell_session_id=1,
        provider_session_id=1,
        provider_entrypoint_sha256=producer.PROVIDER_ENTRYPOINT_SHA256["codex"],
    )
    claim = receipt["claim"]
    assert claim["tree_root_pid"] == claim["window_pid"] == 200
    assert claim["provider_pid"] == 300
    assert claim["process_tree_projection"] == _tree()
    assert claim["process_tree_sha256"] == producer.sha256_bytes(
        producer.canonical_json(_tree())
    )
    assert receipt["digest"] == producer.sha256_bytes(producer.canonical_json(claim))


def test_projected_pid_reader_accepts_mapping_rows_and_rejects_malformed() -> None:
    assert producer._projected_pids(_tree()) == {200, 250, 300}
    with pytest.raises(producer.ProducerError, match="process_tree_projection_invalid"):
        producer._projected_pids([{"parent_pid": None, "exe_name": "missing-pid"}])


def test_provider_process_rejects_wrong_implementation_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = SimpleNamespace(
        exe=lambda: r"C:\wrong.exe",
        cmdline=lambda: [r"C:\wrong.exe"],
        is_running=lambda: True,
    )
    monkeypatch.setattr(producer.psutil, "Process", lambda _pid: fake)
    runtime = producer.ProviderRuntime((r"C:\approved.exe",), producer.Path(r"C:\approved.exe"))
    with pytest.raises(producer.ProducerError, match="provider_process_executable_mismatch"):
        producer._verify_provider_process(10, runtime)


def test_provider_process_projection_is_derived_from_native_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = producer.ProviderRuntime((r"C:\approved.exe",), producer.Path(r"C:\approved.exe"))
    expected = producer.provider_argv("codex", "nonce-prompt")
    fake = SimpleNamespace(
        exe=lambda: r"C:\approved.exe",
        cmdline=lambda: [r"C:\approved.exe", *expected[1:]],
    )
    monkeypatch.setattr(producer.psutil, "Process", lambda _pid: fake)
    assert producer._verify_provider_process(
        10,
        runtime,
        provider="codex",
        expected_argv=expected,
        expected_nonce="nonce-prompt",
    ) == [r"C:\approved.exe".split("\\")[-1], *expected[1:]]
    fake.cmdline = lambda: [r"C:\approved.exe", "exec", "nonce-prompt"]
    with pytest.raises(producer.ProducerError, match="provider_process_argv_mismatch"):
        producer._verify_provider_process(
            10,
            runtime,
            provider="codex",
            expected_argv=expected,
            expected_nonce="nonce-prompt",
        )


def test_concurrency_barrier_rejects_agent_that_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = SimpleNamespace(is_running=lambda: False)
    monkeypatch.setattr(producer.psutil, "Process", lambda _pid: fake)
    state = {
        "provider_pid": 10,
        "runtime": producer.ProviderRuntime(
            (r"C:\approved.exe",), producer.Path(r"C:\approved.exe")
        ),
        "nonce": "a" * 64,
    }
    with pytest.raises(producer.ProducerError, match="provider_concurrency_barrier_failed"):
        producer._assert_concurrent_provider_barrier([state])


@pytest.mark.parametrize("offset", [timedelta(hours=-1), timedelta(minutes=6)])
def test_rung_time_rejects_stale_or_future_interval(offset: timedelta) -> None:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    started = now + offset
    completed = started + timedelta(seconds=1)
    with pytest.raises(producer.ProducerError, match="rung_time_invalid"):
        producer.validate_interval(started, completed, now=now)


def test_cleanup_rejects_surviving_process(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        pid = 44

        def is_running(self) -> bool:
            return True

        def create_time(self) -> float:
            return 1.0

        def exe(self) -> str:
            return r"C:\owned.exe"

        def children(self, recursive: bool = False) -> list[object]:  # noqa: ARG002
            return []

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    monkeypatch.setattr(producer.psutil, "Process", lambda _pid: FakeProcess())
    monkeypatch.setattr(producer, "_process_session_id", lambda _pid: 3)
    identity = producer.NativeProcessIdentity(44, 1.0, r"C:\owned.exe", 3)
    monkeypatch.setattr(producer, "capture_process_identity", lambda _pid: identity)
    monkeypatch.setattr(
        producer.psutil,
        "wait_procs",
        lambda values, timeout: ([], values),  # noqa: ARG005
    )
    with pytest.raises(producer.ProducerError, match="cleanup_incomplete"):
        producer.cleanup_process_tree([identity], wait_s=0)


def test_cleanup_reports_no_remaining_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        pid = 44

        def is_running(self) -> bool:
            return True

        def create_time(self) -> float:
            return 1.0

        def exe(self) -> str:
            return r"C:\owned.exe"

        def children(self, recursive: bool = False) -> list[object]:  # noqa: ARG002
            return []

        def terminate(self) -> None:
            pass

    monkeypatch.setattr(producer.psutil, "Process", lambda _pid: FakeProcess())
    monkeypatch.setattr(producer, "_process_session_id", lambda _pid: 3)
    identity = producer.NativeProcessIdentity(44, 1.0, r"C:\owned.exe", 3)
    monkeypatch.setattr(producer, "capture_process_identity", lambda _pid: identity)
    monkeypatch.setattr(
        producer.psutil,
        "wait_procs",
        lambda values, timeout: (values, []),  # noqa: ARG005
    )
    receipt = producer.cleanup_process_tree([identity], wait_s=0)
    assert receipt["completed"] is True
    assert receipt["remaining_count"] == 0


def test_cleanup_never_terminates_reused_pid_victim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class ReusedVictim:
        pid = 44

        def is_running(self) -> bool:
            return True

        def create_time(self) -> float:
            return 2.0

        def exe(self) -> str:
            return r"C:\victim.exe"

        def children(self, recursive: bool = False) -> list[object]:  # noqa: ARG002
            return []

        def terminate(self) -> None:
            calls.append("terminate")

        def kill(self) -> None:
            calls.append("kill")

    monkeypatch.setattr(producer.psutil, "Process", lambda _pid: ReusedVictim())
    monkeypatch.setattr(producer, "_process_session_id", lambda _pid: 3)
    original = producer.NativeProcessIdentity(44, 1.0, r"C:\owned.exe", 3)
    receipt = producer.cleanup_process_tree([original], wait_s=0)
    assert calls == []
    assert receipt["target_count"] == 0
    assert receipt["identity_mismatch_count"] == 1


def test_disposable_terminal_proof_rejects_preexisting_or_unowned_window() -> None:
    owned = {
        "pid": 20,
        "title": "SC_SCALE codex realcodex-1 n",
        "exe_name": producer.TERMINAL_EXE,
    }
    with pytest.raises(producer.ProducerError, match="disposable_terminal_host_not_established"):
        producer.assert_disposable_terminal_host(
            preexisting_terminal_pids={20},
            launched_terminal_pids={20},
            launched_titles={str(owned["title"])},
            observed_terminal_windows=[owned],
        )
    unowned = {"pid": 20, "title": "User terminal", "exe_name": producer.TERMINAL_EXE}
    with pytest.raises(producer.ProducerError, match="disposable_terminal_host_not_established"):
        producer.assert_disposable_terminal_host(
            preexisting_terminal_pids=set(),
            launched_terminal_pids={20},
            launched_titles={str(owned["title"])},
            observed_terminal_windows=[owned, unowned],
        )
    assert producer.assert_disposable_terminal_host(
        preexisting_terminal_pids=set(),
        launched_terminal_pids={20},
        launched_titles={str(owned["title"])},
        observed_terminal_windows=[owned],
    )["safe_to_terminate_launched_hosts"] is True


def test_rendered_terminal_ack_is_explicitly_derivative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = iter(("a" * 64, "b" * 64))
    monkeypatch.setattr(producer.secrets, "token_hex", lambda _size: next(ids))
    first = datetime(2026, 7, 17, 1, tzinfo=UTC)
    second = first + timedelta(milliseconds=1)
    observations = producer.build_ack_observations(
        stdout_line="ACK",
        stdout_captured_at=first,
        rendered_line="ACK",
        rendered_captured_at=second,
    )
    assert set(observations) == {"process_stdout", "rendered_terminal_copy"}
    assert observations["rendered_terminal_copy"]["derivative_of_event_id"] == (
        observations["process_stdout"]["event_id"]
    )
    assert "independent" not in producer.canonical_json(observations).decode()


def test_provider_wrapper_clears_environment_at_actual_child_process(
    tmp_path: producer.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in _source_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    runtime = producer.ProviderRuntime((r"C:\codex.exe",), producer.Path(r"C:\codex.exe"))
    script, *_rest = producer._write_agent_script(
        tmp_path,
        "codex",
        "realcodex-1",
        "n" * 64,
        "prompt",
        tmp_path / "agent.log",
        admin_policy=tmp_path / "deny.toml",
        runtime=runtime,
    )
    text = script.read_text(encoding="utf-8")
    assert "$psi.EnvironmentVariables.Clear()" in text
    assert "constructed_initial_environment_names" in text
    assert "Start-Process" not in text
    assert "secret" not in text


def test_requested_runner_config_does_not_assert_isolation_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    merged_consumer_sha = "b" * 40
    monkeypatch.setattr(producer, "ECOSYSTEM_CONTRACT_SHA", merged_consumer_sha)
    values = {
        "GITHUB_REPOSITORY": "rblake2320/selfconnect",
        "GITHUB_WORKFLOW": "Restricted Real-Agent Scale Producer",
        "SCALE_PRODUCER_ENVIRONMENT": "scale-readiness-producer",
        "SCALE_RUNNER_GROUP": "selfconnect-scale-ephemeral",
        "SCALE_PRODUCER_JOB": "restricted-scale-producer",
        "GITHUB_REF": "refs/heads/master",
        "ECOSYSTEM_CONTRACT_SHA": merged_consumer_sha,
        "GITHUB_SHA": "b" * 40,
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_ACTOR": "owner",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    context = producer._workflow_context()
    config = producer._requested_runner_config()
    encoded = producer.canonical_json(config).decode()
    assert config == {
        "environment": "scale-readiness-producer",
        "runner_group": "selfconnect-scale-ephemeral",
    }
    assert set(context) == {
        "repository",
        "workflow",
        "ref",
        "producer_run_id",
        "producer_run_attempt",
        "actor",
        "ecosystem_contract_sha",
        "core_head_sha",
    }
    assert "ephemeral_runner" not in encoded
    assert "dedicated_runner" not in encoded
    assert "sensitive_repositories_present" not in encoded


def test_workflow_context_fails_closed_until_consumer_main_is_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECOSYSTEM_CONTRACT_SHA", "CONSUMER_MAIN_SHA_REQUIRED")
    with pytest.raises(producer.ProducerError, match="consumer_contract_not_pinned"):
        producer._workflow_context()


def test_committed_contract_fixture_is_generator_output(tmp_path: producer.Path) -> None:
    generated = tmp_path / "fixture"
    producer.write_contract_fixture(generated)
    committed = (
        producer.ROOT / "tests" / "fixtures" / "restricted_scale_producer_bundle"
    )
    expected_names = {
        "manifest.json",
        "rung-10.json",
        "rung-15.json",
        "rung-20.json",
        "vector.json",
    }
    assert {path.name for path in generated.iterdir()} == expected_names
    assert {path.name for path in committed.iterdir()} == expected_names
    for name in expected_names:
        assert (generated / name).read_bytes() == (committed / name).read_bytes()
    vector = producer.json.loads((committed / "vector.json").read_text(encoding="utf-8"))
    assert vector["generator_source_sha256"] == producer.sha256_file(producer.Path(producer.__file__))
    assert vector["bundle_files"] == {
        name: producer.sha256_file(committed / name)
        for name in sorted(expected_names - {"vector.json"})
    }
