"""Measure real Ollama GPU residency from a cold start.

This deliberately has no simulated backend.  It refuses to run without
NVIDIA SMI, Ollama, the requested model, and an idle Ollama model list.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOCK_PATH = Path(os.environ.get("LOCALAPPDATA", ".")) / "SelfConnect" / "vram-cold-start.lock"


def acquire_lock() -> int:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            owner = int(LOCK_PATH.read_text(encoding="ascii").strip())
            os.kill(owner, 0)
        except (OSError, ValueError):
            LOCK_PATH.unlink(missing_ok=True)
            descriptor = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        else:
            raise SystemExit(f"another VRAM benchmark owns {LOCK_PATH} (PID {owner})")
    os.write(descriptor, str(os.getpid()).encode("ascii"))
    return descriptor


def run_text(*args: str) -> str:
    return subprocess.check_output(args, text=True, timeout=30).strip()


def gpu_snapshot() -> dict[str, int]:
    line = run_text(
        "nvidia-smi",
        "--query-gpu=memory.total,memory.used,memory.free",
        "--format=csv,noheader,nounits",
    ).splitlines()[0]
    total, used, free = (int(part.strip()) for part in line.split(","))
    return {"total_mb": total, "used_mb": used, "free_mb": free}


def loaded_models() -> list[str]:
    lines = run_text("ollama", "ps").splitlines()
    return [line.split()[0] for line in lines[1:] if line.strip()]


def installed_models() -> set[str]:
    lines = run_text("ollama", "list").splitlines()
    return {line.split()[0] for line in lines[1:] if line.strip()}


def stop(model: str) -> None:
    subprocess.run(
        ["ollama", "stop", model],
        check=False,
        capture_output=True,
        timeout=30,
    )
    deadline = time.monotonic() + 60
    while model in loaded_models():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"model did not unload: {model}")
        time.sleep(0.5)


def generate(model: str, context: int) -> dict[str, Any]:
    payload = {
        "model": model,
        "prompt": "Reply with exactly OK.",
        "stream": False,
        "keep_alive": "5m",
        "options": {
            "num_ctx": context,
            "num_predict": 2,
            "temperature": 0,
            "seed": 42,
        },
    }
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=300) as response:
        result = json.loads(response.read().decode())
    result["_wall_seconds"] = round(time.monotonic() - started, 3)
    return result


def measure(model: str, context: int, trial: int) -> dict[str, Any]:
    for resident in loaded_models():
        stop(resident)
    if loaded_models():
        raise RuntimeError("Ollama is not cold before baseline capture")
    time.sleep(2)
    baseline = gpu_snapshot()
    response = generate(model, context)
    time.sleep(2)
    resident = loaded_models()
    if resident != [model]:
        raise RuntimeError(f"unexpected resident Ollama models: {resident}")
    loaded = gpu_snapshot()
    record = {
        "trial": trial,
        "context": context,
        "baseline": baseline,
        "loaded": loaded,
        "delta_used_mb": loaded["used_mb"] - baseline["used_mb"],
        "delta_free_mb": baseline["free_mb"] - loaded["free_mb"],
        "resident_models": resident,
        "response": str(response.get("response", "")).strip(),
        "load_duration_ns": response.get("load_duration"),
        "prompt_eval_count": response.get("prompt_eval_count"),
        "wall_seconds": response["_wall_seconds"],
    }
    stop(model)
    if loaded_models():
        raise RuntimeError("Ollama model remained loaded after trial")
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3.6:27b")
    parser.add_argument("--contexts", nargs="+", type=int, default=[8192, 16384, 32768])
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.model not in installed_models():
        raise SystemExit(f"model is not installed: {args.model}")
    if args.trials < 1 or any(context < 1 for context in args.contexts):
        raise SystemExit("trials and contexts must be positive")

    lock_descriptor = acquire_lock()
    try:
        records = [
            measure(args.model, context, trial)
            for context in args.contexts
            for trial in range(1, args.trials + 1)
        ]
    finally:
        os.close(lock_descriptor)
        LOCK_PATH.unlink(missing_ok=True)
    summaries = {}
    for context in args.contexts:
        rows = [row for row in records if row["context"] == context]
        deltas = [row["delta_used_mb"] for row in rows]
        summaries[str(context)] = {
            "trials": len(rows),
            "delta_used_mb_min": min(deltas),
            "delta_used_mb_max": max(deltas),
            "delta_used_mb_mean": round(statistics.fmean(deltas), 2),
            "delta_used_mb_stddev": round(statistics.pstdev(deltas), 2),
            "minimum_free_mb_after_load": min(row["loaded"]["free_mb"] for row in rows),
        }
    report = {
        "schema": "selfconnect.vram-cold-start.v1",
        "run_id": uuid.uuid4().hex,
        "captured_at": datetime.now(UTC).isoformat(),
        "method": {
            "cold_start_each_trial": True,
            "baseline_subtracted": True,
            "real_hardware_only": True,
            "temperature": 0,
            "seed": 42,
        },
        "gpu_name": run_text(
            "nvidia-smi", "--query-gpu=name", "--format=csv,noheader"
        ).splitlines()[0],
        "model": args.model,
        "summaries": summaries,
        "trials": records,
        "ok": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
