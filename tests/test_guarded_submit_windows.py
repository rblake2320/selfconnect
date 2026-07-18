"""Live interactive-desktop proof for guarded hardware submission.

Set SELFCONNECT_REAL_INTERACTIVE=1 only in an unlocked Windows desktop session.
Hosted CI runners are not evidence for foreground-input behavior.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import sc_guarded_submit as guarded
import sc_mesh_registry
import self_connect as sc


pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("SELFCONNECT_REAL_INTERACTIVE") != "1",
    reason="requires an explicitly enabled interactive Windows desktop",
)


def test_real_receiver_hashes_unicode_stdin_and_returns_signed_ack(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    title = f"SC_GUARDED_{os.getpid()}_{time.time_ns()}"
    pipe = guarded.make_private_pipe_address()
    key = os.urandom(32)
    ready = tmp_path / "ready.txt"
    receiver_script = tmp_path / "receiver.py"
    receiver_script.write_text(
        """
import ctypes, hashlib, os, sys, threading, time, traceback
from pathlib import Path
from sc_guarded_submit import AckKeyRing, ProcessingAckServer, SubprocessAckProcessor

ctypes.windll.kernel32.SetConsoleTitleW(os.environ['SC_TITLE'])
hwnd = ctypes.windll.kernel32.GetConsoleWindow()
Path(os.environ['SC_READY']).write_text(str(hwnd), encoding='ascii')
stop_focus = threading.Event()
def hold_focus():
    while not stop_focus.is_set():
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.02)
threading.Thread(target=hold_focus, daemon=True).start()
actual = input()
stop_focus.set()
os.environ['SC_ACTUAL_DIGEST'] = hashlib.sha256(actual.encode('utf-8')).hexdigest()
adapter = Path(os.environ['SC_READY']).with_suffix('.adapter.py')
adapter.write_text("import json,os,sys\\np=json.load(sys.stdin)\\nprint(json.dumps({'admission_id':p['admission_id'],'mode':p['mode'],'decision':'accepted','input_sha256':os.environ['SC_ACTUAL_DIGEST']}))\\n", encoding='ascii')
keyring = AckKeyRing({os.environ['SC_KEY_ID']: bytes.fromhex(os.environ['SC_KEY'])})
server = ProcessingAckServer(os.environ['SC_PIPE'], keyring, os.environ['SC_KEY_ID'], os.environ['SC_ADMISSION'])
try:
    server.serve_once(SubprocessAckProcessor([sys.executable, str(adapter)]))
except Exception:
    Path(os.environ['SC_ERROR']).write_text(traceback.format_exc(), encoding='utf-8')
    raise
""",
        encoding="utf-8",
    )
    env = dict(os.environ)
    key_id = "live-key-2026-07"
    keyring = guarded.AckKeyRing({key_id: key})
    env.update({
        "SC_TITLE": title, "SC_PIPE": pipe, "SC_KEY": key.hex(),
        "SC_KEY_ID": key_id, "SC_READY": str(ready),
        "SC_ADMISSION": str(tmp_path / "receiver-admission.sqlite3"),
        "SC_ERROR": str(tmp_path / "receiver-error.txt"),
    })
    root = str(Path(__file__).parents[1])
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    process = subprocess.Popen(
        [sys.executable, str(receiver_script)],
        cwd=root,
        env=env,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    try:
        deadline = time.time() + 15
        while time.time() < deadline and not ready.exists():
            time.sleep(0.1)
        assert ready.exists(), "receiver console did not become ready"
        hwnd = int(ready.read_text(encoding="ascii"))
        window = next(item for item in sc.list_windows() if item.hwnd == hwnd)
        identity = guarded.TargetIdentity.from_window(window)
        text = "live unicode \u2603 \U0001f680"
        result = guarded.guarded_submit(
            text,
            target=identity,
            sender="live-controller",
            receiver="live-peer",
            keyring=keyring,
            key_id=key_id,
            ack_pipe=pipe,
            replay_path=tmp_path / "replay.sqlite3",
            event_log_path=tmp_path / "events.jsonl",
        )
        error_path = tmp_path / "receiver-error.txt"
        assert result["ok"] is True, (
            json.dumps(result, default=str, sort_keys=True)
            + ("\n" + error_path.read_text(encoding="utf-8") if error_path.exists() else "")
        )
        assert result["input_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert process.wait(timeout=10) == 0

        deadline = time.time() + 5
        while time.time() < deadline and any(item.hwnd == hwnd for item in sc.list_windows()):
            time.sleep(0.1)
        stale = guarded.guarded_submit(
            "must not type",
            target=identity,
            sender="live-controller",
            receiver="live-peer",
            keyring=keyring,
            key_id=key_id,
            ack_pipe=pipe,
            replay_path=tmp_path / "stale-replay.sqlite3",
            event_log_path=tmp_path / "stale-events.jsonl",
        )
        assert stale["state"] == "refused"
        assert stale["error"] == "target_guard_failed_before_typing"
        evidence_path = os.environ.get("SELFCONNECT_REAL_EVIDENCE_PATH")
        if evidence_path:
            evidence = {
                "schema": "selfconnect.guarded-hardware-submit-live.v5",
                "status": "PASS",
                "claim_status": "candidate evidence only; issue #22 remains open",
                "platform": sys.platform,
                "target": {
                    "exe_name": identity.exe_name,
                    "class_name": identity.class_name,
                    "title_sha256": hashlib.sha256(identity.title.encode("utf-8")).hexdigest(),
                    "exe_path_sha256": hashlib.sha256(identity.exe_path.encode("utf-8")).hexdigest(),
                    "process_start_time_bound": identity.process_start_time_ns > 0,
                },
                "input": {
                    "utf8_bytes": len(text.encode("utf-8")),
                    "sha256": result["input_sha256"],
                    "contains_bmp_unicode": True,
                    "contains_non_bmp_unicode": True,
                },
                "transport": {
                    "name": result["body"]["transport"],
                    "records_requested": result["body"]["records_requested"],
                    "records_written": result["body"]["records_written"],
                    "hardware_enter_events_requested": result["enter"]["events_requested"],
                    "hardware_enter_events_inserted": result["enter"]["events_inserted"],
                },
                "peer_ack": {
                    "schema": result["ack"]["schema"],
                    "key_id": result["ack"]["key_id"],
                    "challenge_echo_matched": result["ack"]["challenge"] == result["challenge"],
                    "attempt_nonce_bound": bool(result["ack"]["attempt_nonce"]),
                    "stable_operation_sha256": result["ack"]["operation_sha256"],
                    "independent_response_key_id": result["ack"]["key_id"],
                    "sender": result["ack"]["sender"],
                    "receiver": result["ack"]["receiver"],
                    "decision": result["decision"],
                    "ack_sha256": result["ack"]["ack_sha256"],
                    "delivery_verified": result["delivery_verified"],
                },
                "durability": {
                    "event_chain_verified": sc_mesh_registry.verify_events(
                        event_log_path=tmp_path / "events.jsonl",
                    )["ok"],
                    "stale_closed_hwnd_refused": stale["state"] == "refused",
                    "sender_finalization_store": "attested SQLite DELETE/FULL catalog; canonical audit envelope pending->audited",
                    "receiver_admission_store": "attested SQLite DELETE/FULL stable full-operation digest excluding per-attempt keys plus durable one-use attempts",
                },
                "pipe_security": {
                    "scope": "current logon SID, local clients only",
                    "first_instance": True,
                    "client_token_logon_sid_checked": True,
                    "overlapped_total_deadline": True,
                    "overlapped_buffers_retained_until_kernel_completion": True,
                    "native_deadline_pre_call_checks": True,
                    "native_deadline_completion_boundary": "entered native calls may complete after the deadline; post-call expiry is ambiguous",
                },
                "processor": {
                    "direct_child_killed_on_deadline": True,
                    "stdin_stdout_share_processor_deadline": True,
                    "descendants_prohibited_but_not_contained": True,
                    "digest_is_adapter_attestation": True,
                    "durable_admission_idempotency_required": True,
                },
                "claim_boundaries": {
                    "issue_22_open": True,
                    "authority_scope": "exported guarded_submit under a trusted non-monkeypatched interpreter; private helpers and module mutation excluded",
                    "production_readiness_claimed": False,
                    "windows_terminal_per_tab_claimed": False,
                    "logical_sender_is_process_identity_claim": False,
                    "protected_parent_directory_is_deployer_precondition": True,
                },
                "implementation_evidence": {
                    "commit_scope": "implementation prior-head captured before committing this refreshed artifact",
                    "git_head": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True,
                    ).strip(),
                    "sc_guarded_submit_sha256": hashlib.sha256(
                        (repo_root / "sc_guarded_submit.py").read_bytes(),
                    ).hexdigest(),
                    "self_connect_sha256": hashlib.sha256(
                        (repo_root / "self_connect.py").read_bytes(),
                    ).hexdigest(),
                },
                "secrets_or_raw_input_included": False,
            }
            output = Path(evidence_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
