"""Fresh-Qwen integrated Capability OS release-candidate proof."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sc_local_agent_harness import ToolContract  # noqa: E402
from sc_local_agent_runtime import LocalAgentRuntime, RuntimeConfig, tool_schemas  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="qwen3.6:27b")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    run_id = uuid.uuid4().hex
    state = output.parent / f"{output.stem}-state-{run_id[:12]}"
    repository = state / "owned-repository"
    repository.mkdir(parents=True)
    first = repository / "alpha.txt"
    second = repository / "beta.txt"
    first.write_text(f"ALPHA-{run_id[:12]}", encoding="utf-8")
    second.write_text(f"BETA-{run_id[12:24]}", encoding="utf-8")
    os.environ.update({
        "SC_CAPABILITY_KERNEL": "1",
        "SC_DYNAMIC_SKILLS": "1",
        "SC_TASK_GRAPH": "1",
        "SC_SKILL_LEARNING": "shadow",
        "SC_VISUAL_SPECIALIST": "1",
        "SC_CAPABILITY_GOVERNANCE_PROFILE": "governed",
        "SC_CAPABILITY_STATE_DIR": str(state / "capabilities"),
        "SC_LOCAL_AGENT_STATE_DIR": str(state / "activity"),
        "SELFCONNECT_LOCAL_MODEL_DIR": str(state / "roles"),
        "SELFCONNECT_MESH_DIR": str(state / "mesh"),
    })
    subprocess.run(["ollama", "stop", args.model], capture_output=True, check=False)
    started = time.perf_counter()
    try:
        runtime = LocalAgentRuntime(RuntimeConfig(
            role=f"m7-qwen-{run_id[:8]}",
            model=args.model,
            mesh="capability-os-rc",
            context_window=32_768,
            max_output_tokens=1_024,
            request_timeout_seconds=240,
            temperature=0,
            seed=42,
            repo_root=repository,
        ))
        contract = ToolContract(
            required_tools=(
                "capability_discover",
                "capability_inspect",
                "capability_task_create",
                "capability_task_continue",
            ),
            allowed_tools=(
                "capability_discover",
                "capability_inspect",
                "capability_execute",
                "capability_task_create",
                "capability_task_continue",
            ),
            ordered=True,
            max_retries=1,
            label="m7-live-governed-integration",
        )
        answer = runtime.respond(
            "Read alpha.txt and beta.txt in order using one durable two-step "
            "capability task. Discover the file-reading skill, inspect it, create "
            "the task with named step IDs alpha and beta, with beta depending on "
            "the alpha step ID, continue up to "
            "two steps, then reply with both exact file values and no narration.",
            contract,
        )
        activity = runtime.ledger.history(limit=200)["events"]
        called = [
            event["details"]["tool"]
            for event in activity
            if event.get("event") == "tool_called"
        ]
        tasks = sorted((state / "capabilities" / "tasks").glob("*.json"))
        task = json.loads(tasks[-1].read_text(encoding="utf-8")) if tasks else {}
        meta_tools = [
            item["function"]["name"]
            for item in tool_schemas(
                runtime.harness,
                capability_kernel=True,
                dynamic_skills=True,
                task_graphs=True,
            )
        ]
        evidence = runtime.kernel.evidence.verify()
        expected_values = [first.read_text(encoding="utf-8"), second.read_text(encoding="utf-8")]
        ok = (
            runtime.governance["ok"]
            and meta_tools == list(contract.allowed_tools or ())
            and called[:4] == list(contract.required_tools)
            and "[tool contract blocked" not in answer
            and all(value in answer for value in expected_values)
            and evidence["ok"]
            and task
            and all(step["status"] == "completed" for step in task["steps"])
            and runtime.kernel.shadow_compiler is not None
            and runtime.visual_specialist is not None
        )
        report = {
            "schema": "selfconnect.live-capability-os-rc-proof.v1",
            "run_id": run_id,
            "ok": ok,
            "seconds": round(time.perf_counter() - started, 3),
            "model": args.model,
            "governance": runtime.governance,
            "meta_tools": meta_tools,
            "called_tools": called,
            "answer": answer,
            "expected_values": expected_values,
            "task": task,
            "evidence": evidence,
            "shadow_compiler_integrated": runtime.kernel.shadow_compiler is not None,
            "visual_specialist_integrated": runtime.visual_specialist is not None,
            "mcp_bridge_count": len(runtime.mcp_bridges),
            "mcp_live_proof": "proofs/capability_os/m4_live_qwen_mcp_20260725.json",
            "visual_live_proofs": [
                "proofs/capability_os/m5_live_visual_specialist_20260725.json",
                "proofs/capability_os/m5_live_visual_specialist_repeat2_20260725.json",
                "proofs/capability_os/m5_live_visual_specialist_repeat3_20260725.json",
            ],
            "shadow_live_proofs": [
                "proofs/capability_os/m6_live_shadow_skill_20260725.json",
                "proofs/capability_os/m6_live_shadow_skill_repeat2_20260725.json",
                "proofs/capability_os/m6_live_shadow_skill_repeat3_20260725.json",
            ],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0 if ok else 1
    finally:
        subprocess.run(["ollama", "stop", args.model], capture_output=True, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
