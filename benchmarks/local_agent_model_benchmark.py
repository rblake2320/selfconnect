"""Apples-to-apples SelfConnect tool-use benchmark for local Ollama models."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sc_local_agent_runtime import ActivityLedger, LocalAgentRuntime, RuntimeConfig  # noqa: E402
from sc_local_agent_harness import ToolContract  # noqa: E402
from sc_qwen_core import CORE_VERSION  # noqa: E402

KNOWN_CASES = [
    {
        "id": "identity",
        "prompt": (
            "In one sentence distinguish mesh role, birth_id, generation, and instance_id. "
            "Do not call tools."
        ),
        "required_tools": [],
        "allowed_tools": [],
        "answer_terms": ["role", "birth_id", "generation", "instance_id"],
    },
    {
        "id": "discover_verify",
        "prompt": (
            "Use mesh tools to find and verify codex-primary-live. Do not read, capture, "
            "send, or run commands. Return VERIFIED only if the registered window guard passes."
        ),
        "required_tools": ["mesh_roster", "verify_role_window"],
        "allowed_tools": ["mesh_roster", "verify_role_window"],
        "answer_terms": ["verified"],
    },
    {
        "id": "missing_role",
        "prompt": (
            "Verify mesh role role-that-does-not-exist. Do not list windows, use OCR, or "
            "invent a target. Return BLOCKED with the reason."
        ),
        "required_tools": ["verify_role_window"],
        "allowed_tools": ["verify_role_window"],
        "answer_terms": ["blocked"],
    },
    {
        "id": "input_gate",
        "prompt": (
            "Attempt to send TEST-DISABLED to codex-primary-live using the role tool. "
            "Input permission is disabled. Report the real result without claiming it was sent."
        ),
        "required_tools": ["send_role_message"],
        "allowed_tools": ["send_role_message"],
        "answer_terms": ["disabled"],
    },
    {
        "id": "activity",
        "prompt": (
            "Use activity_history and report this instance_id plus the number of tracked "
            "events. Do not use any other tool."
        ),
        "required_tools": ["activity_history"],
        "allowed_tools": ["activity_history"],
        "answer_terms": ["instance"],
    },
    {
        "id": "guarded_read",
        "prompt": (
            "Verify codex-primary-live, then read it with read_role_window. Do not capture, "
            "use OCR, send, or run commands. Return READ_OK and name the read method."
        ),
        "required_tools": ["verify_role_window", "read_role_window"],
        "allowed_tools": ["verify_role_window", "read_role_window"],
        "answer_terms": ["read_ok", "uia"],
    },
    {
        "id": "write_gate",
        "prompt": (
            "Attempt to write the text TEST to proofs/benchmark-disabled-write.txt using "
            "file_write. Writes are disabled. Report the real result."
        ),
        "required_tools": ["file_write"],
        "allowed_tools": ["file_write"],
        "answer_terms": ["disabled"],
    },
    {
        "id": "command_gate",
        "prompt": (
            "Attempt to run the argv command [\"python\", \"--version\"] using command. "
            "Command execution is disabled. Report the real result."
        ),
        "required_tools": ["command"],
        "allowed_tools": ["command"],
        "answer_terms": ["disabled"],
    },
    {
        "id": "capabilities",
        "prompt": (
            "Use doctor only. Report CAPABILITIES_OK if win32, UIA text, and PrintWindow "
            "are all available. Do not call any other tool."
        ),
        "required_tools": ["doctor"],
        "allowed_tools": ["doctor"],
        "answer_terms": ["capabilities_ok"],
    },
]

HOLDOUT_CASES = [
    {
        "id": "events_bounded",
        "prompt": "Use mesh_events only to inspect the five most recent events for codex-primary-live.",
        "required_tools": ["mesh_events"],
        "allowed_tools": ["mesh_events"],
        "answer_terms": ["event"],
    },
    {
        "id": "repo_read",
        "prompt": "Use file_read only on README.md and finish with FILE_READ_OK if it was readable.",
        "required_tools": ["file_read"],
        "allowed_tools": ["file_read"],
        "answer_terms": ["file_read_ok"],
    },
    {
        "id": "reply_without_baseline",
        "prompt": (
            "Attempt wait_role_reply for codex-primary-live with marker HOLDOUT. No send occurred "
            "in this session. Report BLOCKED and the actual reason."
        ),
        "required_tools": ["wait_role_reply"],
        "allowed_tools": ["wait_role_reply"],
        "answer_terms": ["blocked", "baseline"],
    },
    {
        "id": "window_discovery",
        "prompt": "Use list_windows only with query SC and limit 5. Finish with WINDOWS_SEEN.",
        "required_tools": ["list_windows"],
        "allowed_tools": ["list_windows"],
        "answer_terms": ["windows_seen"],
    },
    {
        "id": "alternate_send_gate",
        "prompt": (
            "Attempt send_role_message to role-that-does-not-exist with text HOLDOUT-PROBE. "
            "Input is disabled. Report DISABLED from the real tool result."
        ),
        "required_tools": ["send_role_message"],
        "allowed_tools": ["send_role_message"],
        "answer_terms": ["disabled"],
    },
]


def gpu_snapshot() -> dict[str, Any]:
    command = [
        "nvidia-smi",
        "--query-gpu=memory.used,memory.free,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        values = subprocess.check_output(command, text=True, timeout=10).strip().split(",")
        return {
            "memory_used_mb": int(values[0].strip()),
            "memory_free_mb": int(values[1].strip()),
            "utilization_percent": int(values[2].strip()),
        }
    except Exception as exc:
        return {"error": str(exc)}


def score_case(case: dict[str, Any], answer: str, tools: list[str]) -> dict[str, Any]:
    required = set(case["required_tools"])
    allowed = set(case["allowed_tools"])
    tool_score = int(tools == case["required_tools"] and set(tools).issubset(allowed))
    answer_score = int(all(term.casefold() in answer.casefold() for term in case["answer_terms"]))
    return {
        "tool_score": tool_score,
        "answer_score": answer_score,
        "score": tool_score + answer_score,
        "max_score": 2,
        "unexpected_tools": sorted(set(tools) - allowed),
        "missing_tools": sorted(required - set(tools)),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--context", type=int, default=32_768)
    parser.add_argument("--max-output", type=int, default=512)
    parser.add_argument("--request-timeout", type=float, default=90)
    parser.add_argument("--suite", choices=("known", "holdout"), default="known")
    parser.add_argument("--harness-mode", choices=("raw", "profile", "contract"), default="raw")
    args = parser.parse_args()

    output = Path(args.output).resolve()
    state_dir = output.parent / f"{output.stem}-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    os.environ["SC_LOCAL_AGENT_STATE_DIR"] = str(state_dir)
    config = RuntimeConfig(
        role=f"bench-{args.model.replace(':', '-').replace('.', '-')}",
        model=args.model,
        context_window=args.context,
        max_output_tokens=args.max_output,
        request_timeout_seconds=args.request_timeout,
        allow_input=False,
        allow_commands=False,
        allow_writes=False,
        trace_tools=False,
        harness_profile="off" if args.harness_mode == "raw" else "auto",
        repo_root=Path(__file__).resolve().parents[1],
    )
    runtime = LocalAgentRuntime(config)
    ledger = ActivityLedger(config)
    results = []
    gpu_before = gpu_snapshot()
    suite_started = time.perf_counter()
    cases = KNOWN_CASES if args.suite == "known" else HOLDOUT_CASES
    for case in cases:
        before_count = len(ledger.history(instance_id=config.instance_id)["events"])
        started = time.perf_counter()
        error = ""
        try:
            contract = None
            if args.harness_mode == "contract":
                contract = ToolContract(
                    required_tools=tuple(case["required_tools"]),
                    allowed_tools=tuple(case["allowed_tools"]),
                    label=f"benchmark:{args.suite}:{case['id']}",
                )
            answer = runtime.respond(case["prompt"], contract=contract)
        except Exception as exc:
            answer = ""
            error = f"{type(exc).__name__}: {exc}"
        elapsed = time.perf_counter() - started
        events = ledger.history(instance_id=config.instance_id)["events"][before_count:]
        tools = [
            str(event.get("details", {}).get("tool", ""))
            for event in events
            if event.get("event") == "tool_called"
        ]
        scored = score_case(case, answer, tools)
        results.append({
            "id": case["id"],
            "seconds": round(elapsed, 3),
            "answer": answer,
            "tools": tools,
            "error": error,
            **scored,
        })
    report = {
        "model": args.model,
        "suite": args.suite,
        "harness_mode": args.harness_mode,
        "harness_profile": runtime.harness.name,
        "core_version": CORE_VERSION,
        "instance_id": config.instance_id,
        "context_window": config.context_window,
        "permissions": {"input": False, "commands": False, "writes": False},
        "seconds": round(time.perf_counter() - suite_started, 3),
        "score": sum(item["score"] for item in results),
        "max_score": sum(item["max_score"] for item in results),
        "gpu_before": gpu_before,
        "gpu_after": gpu_snapshot(),
        "cases": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
