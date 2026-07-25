"""Repeated cold-start model benchmark with constraint-first selection."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = REPO_ROOT / "benchmarks" / "local_agent_model_benchmark.py"


def _run(command: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _unload(model: str) -> None:
    result = _run(["ollama", "stop", model], timeout=30)
    if result.returncode:
        raise RuntimeError(f"failed to unload {model}: {result.stderr.strip()}")
    deadline = time.time() + 30
    while time.time() < deadline:
        active = _run(["ollama", "ps"], timeout=10)
        if model.casefold() not in active.stdout.casefold():
            _wait_for_gpu_settle()
            return
        time.sleep(0.5)
    raise RuntimeError(f"model remained loaded after stop: {model}")


def _gpu_used_mb() -> int:
    result = _run([
        "nvidia-smi",
        "--query-gpu=memory.used",
        "--format=csv,noheader,nounits",
    ], timeout=10)
    if result.returncode:
        raise RuntimeError(f"nvidia-smi failed: {result.stderr.strip()}")
    return int(result.stdout.strip().splitlines()[0])


def _wait_for_gpu_settle() -> None:
    deadline = time.time() + 30
    samples: list[int] = []
    while time.time() < deadline:
        samples.append(_gpu_used_mb())
        if len(samples) >= 3 and max(samples[-3:]) - min(samples[-3:]) <= 16:
            return
        time.sleep(0.5)
    raise RuntimeError(f"GPU memory did not settle after model unload: {samples[-5:]}")


def _verify_live_role(role: str) -> dict[str, Any]:
    code = (
        "import json;"
        "from pathlib import Path;"
        "from sc_local_agent_runtime import RuntimeConfig,SelfConnectTools;"
        f"r=SelfConnectTools(RuntimeConfig(repo_root=Path.cwd())).verify_role_window({role!r});"
        "print(json.dumps(r));"
        "raise SystemExit(0 if r.get('ok') else 2)"
    )
    result = _run([sys.executable, "-c", code], timeout=30)
    if result.returncode:
        raise RuntimeError(f"live role preflight failed for {role}: {result.stdout}{result.stderr}")
    return json.loads(result.stdout)


def _assert_no_orphan_workers() -> None:
    try:
        import psutil
    except ImportError as exc:
        raise RuntimeError("psutil is required to detect orphan benchmark workers") from exc
    current = os.getpid()
    workers = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        if process.info["pid"] == current:
            continue
        command = " ".join(process.info.get("cmdline") or [])
        if "local_agent_model_benchmark.py" in command:
            workers.append({"pid": process.info["pid"], "command": command})
    if workers:
        raise RuntimeError(f"orphan benchmark workers are still active: {workers}")


def _aggregate(model: str, reports: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [float(report["seconds"]) for report in reports]
    gpu_deltas = [
        report["gpu_after"].get("memory_used_mb", 0)
        - report["gpu_before"].get("memory_used_mb", 0)
        for report in reports
    ]
    case_ids = [item["id"] for item in reports[0]["cases"]]
    per_case = {}
    for case_id in case_ids:
        rows = [
            next(item for item in report["cases"] if item["id"] == case_id)
            for report in reports
        ]
        per_case[case_id] = {
            "runs": len(rows),
            "outcome_pass_rate": sum(row["outcome_score"] for row in rows) / len(rows),
            "safety_pass_rate": sum(row["safety_score"] for row in rows) / len(rows),
            "evidence_pass_rate": sum(row["evidence_score"] for row in rows) / len(rows),
            "trajectory_pass_rate": sum(row["trajectory_score"] for row in rows) / len(rows),
        }
    eligible = all(report["decision"]["eligible"] for report in reports)
    return {
        "model": model,
        "runs": len(reports),
        "eligible": eligible,
        "hard_gate_failures": [
            index + 1
            for index, report in enumerate(reports)
            if not report["decision"]["eligible"]
        ],
        "outcome_mean": statistics.fmean(report["outcome_score"] for report in reports),
        "outcome_max": reports[0]["max_score"] // 3,
        "seconds_mean": statistics.fmean(durations),
        "seconds_median": statistics.median(durations),
        "seconds_stddev": statistics.stdev(durations) if len(durations) > 1 else 0.0,
        "seconds_p95_nearest_rank": sorted(durations)[math.ceil(0.95 * len(durations)) - 1],
        "seconds_max": max(durations),
        "gpu_used_mb_mean": statistics.fmean(
            report["gpu_after"].get("memory_used_mb", 0) for report in reports
        ),
        "gpu_baseline_mb_mean": statistics.fmean(
            report["gpu_before"].get("memory_used_mb", 0) for report in reports
        ),
        "gpu_delta_mb_mean": statistics.fmean(gpu_deltas),
        "gpu_delta_mb_median": statistics.median(gpu_deltas),
        "per_case": per_case,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--suite", choices=("known", "holdout"), default="known")
    parser.add_argument("--harness-mode", choices=("raw", "profile"), default="raw")
    parser.add_argument("--context", type=int, default=32_768)
    parser.add_argument("--max-output", type=int, default=512)
    parser.add_argument("--request-timeout", type=float, default=90)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--live-role", default="codex-primary-live")
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reuse only completed run reports whose fixed configuration matches.",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.runs < 2:
        raise ValueError("--runs must be at least 2 for variance reporting")

    output = Path(args.output).resolve()
    run_dir = output.parent / f"{output.stem}-runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    _assert_no_orphan_workers()
    preflight = _verify_live_role(args.live_role)
    aggregates = []
    all_reports: dict[str, list[str]] = {}
    for model in args.models:
        reports = []
        all_reports[model] = []
        for run_number in range(1, args.runs + 1):
            report_path = run_dir / (
                f"{model.replace(':', '-').replace('/', '-')}-{args.suite}-{run_number:02d}.json"
            )
            if args.resume and report_path.exists():
                prior = json.loads(report_path.read_text(encoding="utf-8"))
                matches = (
                    prior.get("model") == model
                    and prior.get("suite") == args.suite
                    and prior.get("harness_mode") == args.harness_mode
                    and prior.get("context_window") == args.context
                    and prior.get("max_output_tokens", args.max_output) == args.max_output
                    and prior.get("temperature") == args.temperature
                    and prior.get("seed") == args.seed
                )
                if not matches:
                    raise RuntimeError(f"resume configuration mismatch: {report_path}")
                reports.append(prior)
                all_reports[model].append({
                    "path": str(report_path.relative_to(output.parent)),
                    "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                })
                print(
                    f"{model} run {run_number}/{args.runs}: resumed verified report",
                    flush=True,
                )
                continue
            _unload(model)
            partial_path = report_path.with_suffix(
                f".{os.getpid()}.{uuid.uuid4().hex}.partial.json"
            )
            command = [
                sys.executable,
                str(BENCHMARK),
                "--model", model,
                "--output", str(partial_path),
                "--suite", args.suite,
                "--harness-mode", args.harness_mode,
                "--context", str(args.context),
                "--max-output", str(args.max_output),
                "--request-timeout", str(args.request_timeout),
                "--temperature", str(args.temperature),
                "--seed", str(args.seed),
            ]
            result = _run(
                command,
                timeout=args.request_timeout * 20,
            )
            if result.returncode or not partial_path.exists():
                raise RuntimeError(
                    f"{model} run {run_number} failed:\n{result.stdout}\n{result.stderr}"
                )
            os.replace(partial_path, report_path)
            reports.append(json.loads(report_path.read_text(encoding="utf-8")))
            all_reports[model].append({
                "path": str(report_path.relative_to(output.parent)),
                "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            })
            print(
                f"{model} run {run_number}/{args.runs}: "
                f"outcome={reports[-1]['outcome_score']} "
                f"eligible={reports[-1]['decision']['eligible']} "
                f"seconds={reports[-1]['seconds']}",
                flush=True,
            )
        _unload(model)
        aggregates.append(_aggregate(model, reports))

    survivors = [item for item in aggregates if item["eligible"]]
    ranking = sorted(
        survivors,
        key=lambda item: (
            -item["outcome_mean"],
            item["seconds_mean"],
            item["gpu_delta_mb_median"],
        ),
    )
    report = {
        "schema": "selfconnect.local-agent-model-matrix.v1",
        "configuration": {
            "runs": args.runs,
            "suite": args.suite,
            "harness_mode": args.harness_mode,
            "context": args.context,
            "max_output": args.max_output,
            "temperature": args.temperature,
            "seed": args.seed,
            "cold_start_each_run": True,
        },
        "live_role_preflight": preflight,
        "models": aggregates,
        "eligible_ranking": [item["model"] for item in ranking],
        "run_reports": all_reports,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
