"""Real Qwen predecessor/successor proof for Capability OS task recovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sc_local_agent_runtime import LocalAgentRuntime, RuntimeConfig, SelfConnectTools  # noqa: E402
from selfconnect_capabilities import Authority, CapabilityKernel, KernelConfig, TaskStep  # noqa: E402
from selfconnect_capabilities.task_graph import TaskGraph  # noqa: E402

MODEL = "qwen3.6:27b"
ROLE = "qwen-recovery-proof"
TASK_OWNER = f"role:default:{ROLE}"


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def _qwen_ack(instance_id: str, marker: str, state_dir: Path) -> str:
    os.environ["SC_CAPABILITY_KERNEL"] = "0"
    os.environ["SC_LOCAL_AGENT_STATE_DIR"] = str(state_dir / "qwen-ledger")
    runtime = LocalAgentRuntime(
        RuntimeConfig(
            role=ROLE,
            model=MODEL,
            instance_id=instance_id,
            context_window=8_192,
            max_output_tokens=64,
            request_timeout_seconds=180,
            harness_profile="qwen",
            repo_root=REPO_ROOT,
        )
    )
    response = runtime.respond(
        f"You are the live SelfConnect recovery proof process. Reply with exactly {marker}. "
        "Do not call tools."
    )
    if marker not in response:
        raise RuntimeError(f"Qwen did not return required marker {marker!r}: {response!r}")
    return response


def _kernel(
    state_dir: Path,
    *,
    instance_id: str,
    permissions: frozenset[str],
    allow_writes: bool,
) -> tuple[CapabilityKernel, SelfConnectTools]:
    tools = SelfConnectTools(
        RuntimeConfig(
            role=ROLE,
            model=MODEL,
            instance_id=instance_id,
            allow_writes=allow_writes,
            repo_root=REPO_ROOT,
        )
    )
    kernel = CapabilityKernel(
        KernelConfig(enabled=True, task_graphs=True, state_dir=state_dir),
        Authority(f"{ROLE}:{instance_id}", permissions),
        task_owner=TASK_OWNER,
    )
    kernel.bind_adapter("doctor", tools.doctor)
    kernel.bind_adapter("file-write", tools.file_write)
    return kernel, tools


def predecessor(state_dir: Path, signal_path: Path, run_id: str) -> int:
    instance_id = f"predecessor-{uuid.uuid4().hex}"
    qwen = _qwen_ack(instance_id, "QWEN_PREDECESSOR_READY", state_dir)
    kernel, _tools = _kernel(
        state_dir,
        instance_id=instance_id,
        permissions=frozenset({"observe.system", "write.file"}),
        allow_writes=True,
    )
    marker_relative = "proofs/capability_os/m3_recovery_marker.txt"
    marker_content = f"SelfConnect M3 recovery marker {run_id}\n"
    graph = kernel.new_task(
        "prove completed mutation is not repeated after Qwen replacement",
        task_id="m3-live-main",
        max_total_attempts=3,
        max_attempts_per_step=1,
    )
    kernel.add_task_step(
        graph,
        "selfconnect.file-write",
        {"path": marker_relative, "content": marker_content},
        step_id="write-marker",
    )
    kernel.add_task_step(
        graph,
        "selfconnect.doctor",
        {},
        depends_on=("write-marker",),
        step_id="successor-doctor",
    )
    first = kernel.run_ready(graph)
    if first["executed"][0]["status"] != "completed":
        raise RuntimeError(f"predecessor mutation did not complete: {first}")

    ambiguous = kernel.new_task(
        "prove ambiguous interrupted mutation blocks",
        task_id="m3-live-ambiguous",
    )
    kernel.add_task_step(
        ambiguous,
        "selfconnect.file-write",
        {
            "path": "proofs/capability_os/m3_ambiguous_must_not_exist.txt",
            "content": "must never be written\n",
        },
        step_id="ambiguous-write",
    )
    ambiguous.start("ambiguous-write", f"ambiguous-{run_id}")
    marker = REPO_ROOT / marker_relative
    _write_json(
        signal_path,
        {
            "ok": True,
            "pid": os.getpid(),
            "instance_id": instance_id,
            "qwen_response": qwen,
            "main_checkpoint_hash": graph.checkpoint_hash,
            "ambiguous_checkpoint_hash": ambiguous.checkpoint_hash,
            "marker_path": marker_relative,
            "marker_sha256": hashlib.sha256(marker.read_bytes()).hexdigest(),
        },
    )
    while True:
        time.sleep(1)


def successor(state_dir: Path, result_path: Path, run_id: str) -> int:
    instance_id = f"successor-{uuid.uuid4().hex}"
    qwen = _qwen_ack(instance_id, "QWEN_SUCCESSOR_READY", state_dir)
    kernel, _tools = _kernel(
        state_dir,
        instance_id=instance_id,
        permissions=frozenset({"observe.system"}),
        allow_writes=False,
    )
    marker = REPO_ROOT / "proofs/capability_os/m3_recovery_marker.txt"
    marker_before = hashlib.sha256(marker.read_bytes()).hexdigest()
    completed_before = [
        row
        for row in kernel.evidence.records()
        if row["event"] == "capability_completed"
        and row["details"].get("task_id") == "m3-live-main"
        and row["details"].get("step_id") == "write-marker"
    ]
    graph = kernel.load_task("m3-live-main")
    resumed = kernel.run_ready(graph)
    marker_after = hashlib.sha256(marker.read_bytes()).hexdigest()
    completed_after = [
        row
        for row in kernel.evidence.records()
        if row["event"] == "capability_completed"
        and row["details"].get("task_id") == "m3-live-main"
        and row["details"].get("step_id") == "write-marker"
    ]
    write_policy = kernel.authorize("selfconnect.file-write")
    ambiguous = kernel.load_task("m3-live-ambiguous")
    ambiguous_target = REPO_ROOT / "proofs/capability_os/m3_ambiguous_must_not_exist.txt"
    result = {
        "ok": bool(
            graph.summary()["complete"]
            and resumed["executed"][0]["status"] == "completed"
            and marker_before == marker_after
            and len(completed_before) == len(completed_after) == 1
            and not write_policy["allowed"]
            and ambiguous.steps["ambiguous-write"].status == "blocked"
            and not ambiguous_target.exists()
            and kernel.evidence.verify()["ok"]
        ),
        "run_id": run_id,
        "pid": os.getpid(),
        "instance_id": instance_id,
        "qwen_response": qwen,
        "task_owner": TASK_OWNER,
        "main_task": graph.summary(),
        "resume_result": resumed,
        "marker_sha256_before": marker_before,
        "marker_sha256_after": marker_after,
        "write_completion_events_before": len(completed_before),
        "write_completion_events_after": len(completed_after),
        "successor_write_policy": write_policy,
        "ambiguous_task": ambiguous.summary(),
        "ambiguous_step": {
            "status": ambiguous.steps["ambiguous-write"].status,
            "result": ambiguous.steps["ambiguous-write"].result,
            "target_exists": ambiguous_target.exists(),
        },
        "evidence_verification": kernel.evidence.verify(),
        "main_checkpoint_verification": TaskGraph.load(graph.path).summary(),
        "ambiguous_checkpoint_verification": TaskGraph.load(ambiguous.path).summary(),
    }
    _write_json(result_path, result)
    return 0 if result["ok"] else 1


def controller(output: Path) -> int:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    state_dir = output.parent / f".m3-live-state-{run_id}"
    signal = state_dir / "predecessor-ready.json"
    successor_result = state_dir / "successor-result.json"
    state_dir.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, str(Path(__file__).resolve())]
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    predecessor_log = (state_dir / "predecessor.log").open("w", encoding="utf-8")
    first = subprocess.Popen(
        [*command, "--worker", "predecessor", "--state-dir", str(state_dir),
         "--signal", str(signal), "--run-id", run_id],
        cwd=REPO_ROOT,
        stdout=predecessor_log,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    try:
        deadline = time.time() + 240
        while time.time() < deadline and not signal.exists() and first.poll() is None:
            time.sleep(0.25)
        if not signal.exists():
            raise RuntimeError(
                f"predecessor did not become ready; exit={first.poll()} "
                f"log={(state_dir / 'predecessor.log').read_text(encoding='utf-8')}"
            )
        predecessor_state = json.loads(signal.read_text(encoding="utf-8"))
        first.terminate()
        first.wait(timeout=20)
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=20)
        predecessor_log.close()

    second = subprocess.run(
        [*command, "--worker", "successor", "--state-dir", str(state_dir),
         "--result", str(successor_result), "--run-id", run_id],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=240,
        creationflags=creationflags,
    )
    if second.returncode != 0 or not successor_result.exists():
        raise RuntimeError(
            f"successor failed: exit={second.returncode}\nstdout={second.stdout}\nstderr={second.stderr}"
        )
    successor_state = json.loads(successor_result.read_text(encoding="utf-8"))
    proof = {
        "schema": "selfconnect.capability-os.m3-live-qwen-recovery.v1",
        "created_at": time.time(),
        "model": MODEL,
        "run_id": run_id,
        "predecessor": predecessor_state,
        "predecessor_exit_after_termination": first.returncode,
        "successor": successor_state,
        "checks": {
            "distinct_instances": (
                predecessor_state["instance_id"] != successor_state["instance_id"]
            ),
            "predecessor_was_terminated": first.returncode is not None,
            "successor_rederived_write_denial": (
                successor_state["successor_write_policy"]["allowed"] is False
            ),
            "completed_mutation_not_repeated": (
                successor_state["write_completion_events_before"]
                == successor_state["write_completion_events_after"]
                == 1
            ),
            "marker_unchanged": (
                successor_state["marker_sha256_before"]
                == successor_state["marker_sha256_after"]
            ),
            "ambiguous_mutation_blocked": (
                successor_state["ambiguous_step"]["status"] == "blocked"
                and not successor_state["ambiguous_step"]["target_exists"]
            ),
            "task_completed_from_verified_evidence": successor_state["main_task"]["complete"],
            "evidence_chain_valid": successor_state["evidence_verification"]["ok"],
        },
    }
    proof["ok"] = bool(successor_state["ok"] and all(proof["checks"].values()))
    _write_json(output, proof)
    subprocess.run(["ollama", "stop", MODEL], capture_output=True, text=True, timeout=30)
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0 if proof["ok"] else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", choices=("predecessor", "successor"))
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--signal", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--run-id", default="")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.worker == "predecessor":
        return predecessor(args.state_dir.resolve(), args.signal.resolve(), args.run_id)
    if args.worker == "successor":
        return successor(args.state_dir.resolve(), args.result.resolve(), args.run_id)
    if args.output is None:
        raise SystemExit("--output is required in controller mode")
    return controller(args.output.resolve())


if __name__ == "__main__":
    exit_code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32":
        os._exit(exit_code)
    raise SystemExit(exit_code)
