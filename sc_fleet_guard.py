"""Fleet safety gates for SelfConnect benchmark runs.

This module is intentionally independent from agent sessions. Orchestrators and
dashboards call it to decide when to keep running, capture evidence, or halt a
benchmark stage.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import psutil
import sc_mesh_registry

DEFAULT_RAM_FLOOR_MB = 25 * 1024
DEFAULT_VRAM_FLOOR_MB = 6 * 1024
DEFAULT_ACK_BLOCK_THRESHOLD = 2
DEFAULT_BLOCKED_AGENT_HARD_STOP = 3
DEFAULT_LOCAL_NARRATION_HARD_STOP = 2
DEFAULT_APPROVAL_BLOCK_SECONDS = 90
DEFAULT_P99_LATENCY_MULTIPLIER = 5.0


def default_thresholds() -> dict[str, Any]:
    return {
        "ram_free_mb_floor": DEFAULT_RAM_FLOOR_MB,
        "vram_free_mb_floor": DEFAULT_VRAM_FLOOR_MB,
        "ack_block_threshold": DEFAULT_ACK_BLOCK_THRESHOLD,
        "blocked_agent_hard_stop": DEFAULT_BLOCKED_AGENT_HARD_STOP,
        "local_narration_hard_stop": DEFAULT_LOCAL_NARRATION_HARD_STOP,
        "approval_block_seconds": DEFAULT_APPROVAL_BLOCK_SECONDS,
        "p99_latency_multiplier": DEFAULT_P99_LATENCY_MULTIPLIER,
        "queue_depth_limit": None,
    }


def _mb(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value) / (1024 * 1024), 3)


def resource_snapshot() -> dict[str, Any]:
    mem = psutil.virtual_memory()
    snapshot: dict[str, Any] = {
        "ram_total_mb": _mb(mem.total),
        "ram_free_mb": _mb(mem.available),
        "gpu": None,
    }
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total,memory.used,utilization.gpu,power.draw,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        snapshot["gpu_error"] = str(exc)
        return snapshot
    if proc.returncode != 0 or not proc.stdout.strip():
        snapshot["gpu_error"] = (proc.stderr or proc.stdout).strip() or "nvidia-smi unavailable"
        return snapshot
    first = proc.stdout.strip().splitlines()[0]
    parts = [item.strip() for item in first.split(",")]
    if len(parts) >= 5:
        total = float(parts[0])
        used = float(parts[1])
        snapshot["gpu"] = {
            "vram_total_mb": total,
            "vram_used_mb": used,
            "vram_free_mb": round(total - used, 3),
            "utilization_percent": float(parts[2]),
            "power_watts": float(parts[3]),
            "temperature_c": float(parts[4]),
        }
    return snapshot


def _agent_name(agent: dict[str, Any]) -> str:
    return str(agent.get("name") or agent.get("role") or agent.get("id") or "unknown")


def evaluate_fleet(
    agents: list[dict[str, Any]],
    *,
    resources: dict[str, Any] | None = None,
    thresholds: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
    run_active: bool = True,
    local_models_active: bool = False,
    event_log_ok: bool = True,
) -> dict[str, Any]:
    cfg = default_thresholds()
    if thresholds:
        cfg.update(thresholds)
    resources = resources or {}

    hard_reasons: list[dict[str, Any]] = []
    halt_reasons: list[dict[str, Any]] = []
    capture_triggers: list[dict[str, Any]] = []
    agent_reports: list[dict[str, Any]] = []
    blocked_count = 0

    if run_active and resources.get("ram_free_mb") is not None:
        if float(resources["ram_free_mb"]) < float(cfg["ram_free_mb_floor"]):
            halt_reasons.append({
                "kind": "ram_floor",
                "ram_free_mb": resources["ram_free_mb"],
                "floor_mb": cfg["ram_free_mb_floor"],
            })

    gpu = resources.get("gpu") or {}
    if run_active and local_models_active and gpu.get("vram_free_mb") is not None:
        if float(gpu["vram_free_mb"]) < float(cfg["vram_free_mb_floor"]):
            halt_reasons.append({
                "kind": "vram_floor",
                "vram_free_mb": gpu["vram_free_mb"],
                "floor_mb": cfg["vram_free_mb_floor"],
            })

    if not event_log_ok:
        hard_reasons.append({"kind": "event_log_verify_failed"})

    baseline_p99 = None
    if baseline:
        baseline_p99 = baseline.get("p99_latency_ms")
        if baseline_p99 is None:
            baseline_p99 = baseline.get("delivery_p99_ms")

    for agent in agents:
        name = _agent_name(agent)
        missed_acks = int(agent.get("missed_acks") or 0)
        local_narration = int(agent.get("local_narration_count") or 0)
        approval_block = float(agent.get("approval_block_seconds") or 0)
        queue_depth = agent.get("queue_depth")
        p99_latency = agent.get("p99_latency_ms")
        report = {
            "name": name,
            "status": agent.get("status", "active"),
            "risk": "green",
            "capture": False,
            "reasons": [],
        }

        if missed_acks >= 1:
            report["risk"] = "yellow"
            report["capture"] = True
            report["reasons"].append("missed_ack")
            capture_triggers.append({"agent": name, "kind": "missed_ack", "missed_acks": missed_acks})
        if missed_acks >= int(cfg["ack_block_threshold"]):
            report["risk"] = "red"
            report["status"] = "blocked"
            report["reasons"].append("ack_block_threshold")
            blocked_count += 1
        if local_narration >= int(cfg["local_narration_hard_stop"]):
            report["risk"] = "red"
            hard_reasons.append({
                "kind": "local_narration_violation",
                "agent": name,
                "count": local_narration,
            })
        if approval_block > float(cfg["approval_block_seconds"]):
            report["risk"] = "red"
            hard_reasons.append({
                "kind": "approval_block_timeout",
                "agent": name,
                "seconds": approval_block,
            })
        if agent.get("wrong_window_guard_failed"):
            report["risk"] = "red"
            hard_reasons.append({"kind": "wrong_window_guard_failed", "agent": name})
        if agent.get("wrong_sender_nonce_accepted"):
            report["risk"] = "red"
            hard_reasons.append({"kind": "wrong_sender_nonce_accepted", "agent": name})
        if agent.get("replay_accepted"):
            report["risk"] = "red"
            hard_reasons.append({"kind": "replay_accepted", "agent": name})
        if agent.get("stale_lease_accepted"):
            report["risk"] = "red"
            hard_reasons.append({"kind": "stale_lease_accepted", "agent": name})
        if cfg.get("queue_depth_limit") is not None and queue_depth is not None:
            if int(queue_depth) > int(cfg["queue_depth_limit"]):
                report["risk"] = "red"
                hard_reasons.append({
                    "kind": "queue_depth_exceeded",
                    "agent": name,
                    "queue_depth": queue_depth,
                    "limit": cfg["queue_depth_limit"],
                })
        if baseline_p99 and p99_latency is not None:
            limit = float(baseline_p99) * float(cfg["p99_latency_multiplier"])
            if float(p99_latency) > limit:
                report["risk"] = "red"
                hard_reasons.append({
                    "kind": "p99_latency_regression",
                    "agent": name,
                    "p99_latency_ms": p99_latency,
                    "limit_ms": limit,
                })
        agent_reports.append(report)

    if blocked_count >= int(cfg["blocked_agent_hard_stop"]):
        hard_reasons.append({
            "kind": "blocked_agent_count",
            "blocked_count": blocked_count,
            "limit": cfg["blocked_agent_hard_stop"],
        })

    if hard_reasons:
        verdict = "hard_stop"
        action = "stop_and_capture"
    elif halt_reasons:
        verdict = "halt_recommended"
        action = "stop_assigning_and_capture"
    elif capture_triggers:
        verdict = "capture"
        action = "capture_and_continue"
    else:
        verdict = "pass"
        action = "continue"

    return {
        "ok": verdict == "pass",
        "verdict": verdict,
        "action": action,
        "hard_reasons": hard_reasons,
        "halt_reasons": halt_reasons,
        "capture_triggers": capture_triggers,
        "blocked_count": blocked_count,
        "agent_reports": agent_reports,
        "resources": resources,
        "thresholds": cfg,
        "created_at": time.time(),
    }


def _event_payload(
    *,
    name: str,
    task: str = "",
    pid: int | None = None,
    hwnd: int | None = None,
    role: str = "",
    birth_id: str = "",
    generation: int | None = None,
    vendor: str = "",
    status: str = "",
    note: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "task": task,
        "pid": pid,
        "hwnd": hwnd,
        "role": role or name,
        "birth_id": birth_id,
        "generation": generation,
        "vendor": vendor,
        "status": status,
        "note": note,
        **(extra or {}),
    }


def fleet_register(
    *,
    name: str,
    task: str = "",
    pid: int | None = None,
    hwnd: int | None = None,
    role: str = "",
    birth_id: str = "",
    generation: int | None = None,
    vendor: str = "",
    event_log_path: str | Path | None = None,
) -> dict[str, Any]:
    data = _event_payload(
        name=name,
        task=task,
        pid=pid,
        hwnd=hwnd,
        role=role,
        birth_id=birth_id,
        generation=generation,
        vendor=vendor,
        status="registered",
    )
    return sc_mesh_registry.append_event(
        "fleet_agent_registered",
        role=data["role"],
        birth_id=birth_id,
        generation=generation,
        agent=vendor,
        hwnd=hwnd,
        task=task,
        status="registered",
        summary=f"fleet agent registered: {name}",
        data=data,
        event_log_path=event_log_path,
    )


def fleet_heartbeat(
    *,
    name: str,
    status: str = "working",
    note: str = "",
    ack_seq: int | None = None,
    latency_ms: float | None = None,
    role: str = "",
    birth_id: str = "",
    generation: int | None = None,
    vendor: str = "",
    hwnd: int | None = None,
    event_log_path: str | Path | None = None,
) -> dict[str, Any]:
    data = _event_payload(
        name=name,
        role=role,
        birth_id=birth_id,
        generation=generation,
        vendor=vendor,
        hwnd=hwnd,
        status=status,
        note=note,
        extra={"ack_seq": ack_seq, "latency_ms": latency_ms},
    )
    return sc_mesh_registry.append_event(
        "fleet_agent_heartbeat",
        role=data["role"],
        birth_id=birth_id,
        generation=generation,
        agent=vendor,
        hwnd=hwnd,
        status=status,
        summary=f"fleet heartbeat: {name}",
        data=data,
        event_log_path=event_log_path,
    )


def fleet_done(
    *,
    name: str,
    result: str,
    reason: str = "",
    final_event_hash: str = "",
    role: str = "",
    birth_id: str = "",
    generation: int | None = None,
    vendor: str = "",
    hwnd: int | None = None,
    event_log_path: str | Path | None = None,
) -> dict[str, Any]:
    data = _event_payload(
        name=name,
        role=role,
        birth_id=birth_id,
        generation=generation,
        vendor=vendor,
        hwnd=hwnd,
        status=result,
        note=reason,
        extra={"result": result, "reason": reason, "final_event_hash": final_event_hash},
    )
    return sc_mesh_registry.append_event(
        "fleet_agent_done",
        role=data["role"],
        birth_id=birth_id,
        generation=generation,
        agent=vendor,
        hwnd=hwnd,
        status=result,
        summary=f"fleet agent done: {name} -> {result}",
        data=data,
        event_log_path=event_log_path,
    )


def _load_json_file(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _print_json(data: dict[str, Any]) -> int:
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


def _print_json_error(kind: str, message: str, **extra: Any) -> int:
    payload = {
        "ok": False,
        "verdict": "input_error",
        "action": "no_op",
        "error": kind,
        "message": message,
    }
    payload.update(extra)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="selfconnect-fleet", description="Evaluate SelfConnect fleet benchmark gates")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("resources")
    p.set_defaults(command="resources")

    p = sub.add_parser("guard")
    p.add_argument("--state-json", required=True, help="JSON with agents/resources/baseline/thresholds")
    p.add_argument("--local-models-active", action="store_true")

    p = sub.add_parser("register")
    p.add_argument("--name", required=True)
    p.add_argument("--task", default="")
    p.add_argument("--pid", type=int, default=None)
    p.add_argument("--hwnd", type=int, default=None)
    p.add_argument("--role", default="")
    p.add_argument("--birth-id", default="")
    p.add_argument("--generation", type=int, default=None)
    p.add_argument("--vendor", default="")
    p.add_argument("--event-log", default="")

    p = sub.add_parser("heartbeat")
    p.add_argument("--name", required=True)
    p.add_argument("--status", default="working")
    p.add_argument("--note", default="")
    p.add_argument("--ack-seq", type=int, default=None)
    p.add_argument("--latency-ms", type=float, default=None)
    p.add_argument("--role", default="")
    p.add_argument("--birth-id", default="")
    p.add_argument("--generation", type=int, default=None)
    p.add_argument("--vendor", default="")
    p.add_argument("--hwnd", type=int, default=None)
    p.add_argument("--event-log", default="")

    p = sub.add_parser("done")
    p.add_argument("--name", required=True)
    p.add_argument("--result", required=True)
    p.add_argument("--reason", default="")
    p.add_argument("--final-event-hash", default="")
    p.add_argument("--role", default="")
    p.add_argument("--birth-id", default="")
    p.add_argument("--generation", type=int, default=None)
    p.add_argument("--vendor", default="")
    p.add_argument("--hwnd", type=int, default=None)
    p.add_argument("--event-log", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "resources":
        return _print_json(resource_snapshot())
    if args.command == "guard":
        try:
            state = _load_json_file(args.state_json)
        except FileNotFoundError:
            return _print_json_error(
                "state_json_missing",
                "fleet guard state file does not exist",
                path=args.state_json,
            )
        except json.JSONDecodeError as exc:
            return _print_json_error(
                "state_json_invalid",
                "fleet guard state file is not valid JSON",
                path=args.state_json,
                line=exc.lineno,
                column=exc.colno,
            )
        return _print_json(evaluate_fleet(
            list(state.get("agents", [])),
            resources=state.get("resources") or resource_snapshot(),
            thresholds=state.get("thresholds"),
            baseline=state.get("baseline"),
            run_active=bool(state.get("run_active", True)),
            local_models_active=bool(args.local_models_active or state.get("local_models_active", False)),
            event_log_ok=bool(state.get("event_log_ok", True)),
        ))
    if args.command == "register":
        return _print_json(fleet_register(
            name=args.name,
            task=args.task,
            pid=args.pid,
            hwnd=args.hwnd,
            role=args.role,
            birth_id=args.birth_id,
            generation=args.generation,
            vendor=args.vendor,
            event_log_path=args.event_log or None,
        ))
    if args.command == "heartbeat":
        return _print_json(fleet_heartbeat(
            name=args.name,
            status=args.status,
            note=args.note,
            ack_seq=args.ack_seq,
            latency_ms=args.latency_ms,
            role=args.role,
            birth_id=args.birth_id,
            generation=args.generation,
            vendor=args.vendor,
            hwnd=args.hwnd,
            event_log_path=args.event_log or None,
        ))
    if args.command == "done":
        return _print_json(fleet_done(
            name=args.name,
            result=args.result,
            reason=args.reason,
            final_event_hash=args.final_event_hash,
            role=args.role,
            birth_id=args.birth_id,
            generation=args.generation,
            vendor=args.vendor,
            hwnd=args.hwnd,
            event_log_path=args.event_log or None,
        ))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
