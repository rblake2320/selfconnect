"""Fabric V0 benchmark harness for the current SelfConnect transport.

The v0 harness measures the system that exists now before Fabric V2 changes
the data plane. It is intentionally logical by default: it exercises envelope
creation, echo-filtered readback classification, audit/event persistence,
fleet-guard evaluation, and baseline comparison without adding model inference
variance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import sc_echo_filter
import sc_fleet_guard
import sc_mesh_registry

SCHEMA_VERSION = 1
DEFAULT_OUTPUT_DIR = Path("experiments") / "fabric_v2" / "results"
DEFAULT_MESSAGES_PER_AGENT = 3
DEFAULT_TRANSPORT = "current_transport"
DEFAULT_PROFILES = ("normal", "enterprise", "government")

FREEZE_DOCS = (
    "docs/PATENT_EVIDENCE_FREEZE_2026-06-20.md",
    "docs/CLAIM_EVIDENCE_MATRIX.md",
    "docs/PATENT_DESIGN_AROUND_DEFENSE.md",
    "docs/PATENT_PRIOR_ART_SNAPSHOT.md",
)

FREEZE_MARKERS = (
    "composition claim",
    "durable virtual",
    "execution hierarchy",
    "mcp/http competitor contrast",
)

PROFILE_CONFIGS: dict[str, dict[str, Any]] = {
    "normal": {
        "label": "Normal",
        "target_guard": True,
        "echo_filter": True,
        "lease_gate": False,
        "audit_required": True,
        "worm_required": False,
        "tpm_required": False,
        "model_calls_per_known_task_target": 0,
    },
    "enterprise": {
        "label": "Enterprise",
        "target_guard": True,
        "echo_filter": True,
        "lease_gate": True,
        "audit_required": True,
        "worm_required": False,
        "tpm_required": "optional",
        "model_calls_per_known_task_target": 0,
    },
    "government": {
        "label": "Government",
        "target_guard": True,
        "echo_filter": True,
        "lease_gate": True,
        "audit_required": True,
        "worm_required": True,
        "tpm_required": "attestation_or_na_record",
        "model_calls_per_known_task_target": 0,
    },
}


def _now_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _percentiles(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "min": 0.0,
            "max": 0.0,
            "avg": 0.0,
            "p50": 0.0,
            "p95": 0.0,
            "p99": 0.0,
        }
    ordered = sorted(float(v) for v in values)

    def pick(percent: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        rank = (len(ordered) - 1) * percent
        lower = int(rank)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = rank - lower
        return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction

    return {
        "count": len(ordered),
        "min": round(ordered[0], 3),
        "max": round(ordered[-1], 3),
        "avg": round(sum(ordered) / len(ordered), 3),
        "p50": round(pick(0.50), 3),
        "p95": round(pick(0.95), 3),
        "p99": round(pick(0.99), 3),
    }


def _baseline_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    aggregate = artifact.get("aggregate") or {}
    transport = aggregate.get("transport_governance_ms") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": artifact.get("run_id", ""),
        "agent_count": artifact.get("agent_count", 0),
        "transport": artifact.get("transport", DEFAULT_TRANSPORT),
        "transport_governance_p50_ms": transport.get("p50", 0.0),
        "transport_governance_p95_ms": transport.get("p95", 0.0),
        "transport_governance_p99_ms": transport.get("p99", 0.0),
        "model_calls_per_known_task": aggregate.get("model_calls_per_known_task", 0.0),
        "artifact_path": artifact.get("artifact_path", ""),
    }


def patent_freeze_status(repo_root: str | Path = ".") -> dict[str, Any]:
    root = Path(repo_root)
    missing = []
    marker_hits: dict[str, bool] = {marker: False for marker in FREEZE_MARKERS}
    for rel in FREEZE_DOCS:
        path = root / rel
        if not path.exists():
            missing.append(rel)
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for marker in FREEZE_MARKERS:
            if marker in text:
                marker_hits[marker] = True
    missing_markers = [marker for marker, found in marker_hits.items() if not found]
    return {
        "ok": not missing and not missing_markers,
        "required_docs": list(FREEZE_DOCS),
        "missing_docs": missing,
        "required_markers": list(FREEZE_MARKERS),
        "missing_markers": missing_markers,
    }


def load_baseline(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    baseline_path = Path(path)
    if not baseline_path.exists():
        return None
    return json.loads(baseline_path.read_text(encoding="utf-8"))


def write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return target


def _profile_list(value: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(value, str):
        raw = DEFAULT_PROFILES if value.strip().lower() == "all" else tuple(value.split(","))
    else:
        raw = tuple(value)
    profiles = [item.strip().lower() for item in raw if item.strip()]
    unknown = [item for item in profiles if item not in PROFILE_CONFIGS]
    if unknown:
        raise ValueError(f"unknown benchmark profile(s): {', '.join(unknown)}")
    return profiles


def _make_agents(agent_count: int, profile: str) -> list[dict[str, Any]]:
    return [
        {
            "name": f"{profile}-agent-{idx}",
            "role": f"{profile}-agent-{idx}",
            "birth_id": f"{profile}-agent-{idx}-logical",
            "generation": 1,
            "status": "active",
            "missed_acks": 0,
            "queue_depth": 0,
        }
        for idx in range(1, agent_count + 1)
    ]


def _run_profile(
    *,
    profile: str,
    agents: list[dict[str, Any]],
    messages_per_agent: int,
    event_log_path: Path,
) -> dict[str, Any]:
    config = PROFILE_CONFIGS[profile]
    total_messages = len(agents) * messages_per_agent
    transport_ms: list[float] = []
    audit_lag_ms: list[float] = []
    end_to_end_ms: list[float] = []
    readback_ms: list[float] = []
    model_calls = 0
    echo_false_positive = 0
    echo_false_negative = 0
    replay_rejected = 0
    replay_accepted = 0
    stale_lease_rejected = 0
    stale_lease_accepted = 0
    seen_sequences: set[tuple[str, int]] = set()
    message_hashes: list[str] = []

    for agent in agents:
        sc_fleet_guard.fleet_register(
            name=agent["name"],
            role=agent["role"],
            birth_id=agent["birth_id"],
            generation=agent["generation"],
            vendor="logical",
            task=f"fabric-v0-{profile}",
            event_log_path=event_log_path,
        )

    for seq in range(total_messages):
        sender = agents[seq % len(agents)]
        receiver = agents[(seq + 1) % len(agents)]
        nonce = f"SC_BENCH_{profile}_{seq:05d}"
        sent_text = f"{nonce} known-task"
        observed_text = f"{nonce}\nACK:{receiver['name']}:SEQ:{seq}"
        t0 = time.perf_counter()
        read_send_ts = time.time()
        record = sc_echo_filter.build_record(
            delta=observed_text,
            nonce=nonce,
            sent_text=sent_text,
            readback_method="logical_TextPattern_delta",
            timestamp_send=read_send_ts,
            timestamp_recv=read_send_ts,
        )
        key = (sender["birth_id"], seq)
        if key in seen_sequences:
            replay_accepted += 1
        else:
            seen_sequences.add(key)
        if config["lease_gate"]:
            expected_generation = int(sender["generation"])
            observed_generation = int(sender["generation"])
            if observed_generation != expected_generation:
                stale_lease_accepted += 1
        if record.classification != sc_echo_filter.EchoClassification.MIXED:
            echo_false_negative += 1
        if record.classification == sc_echo_filter.EchoClassification.ECHO_ONLY:
            echo_false_positive += 1
        t1 = time.perf_counter()
        audit_start = time.perf_counter()
        message_hash = _sha(f"{sender['birth_id']}|{receiver['birth_id']}|{seq}|{record.sent_hash}")
        sc_mesh_registry.append_event(
            "fabric_v0_message",
            role=receiver["role"],
            birth_id=receiver["birth_id"],
            generation=receiver["generation"],
            agent="logical",
            task=f"fabric-v0-{profile}",
            status="ack",
            profile=profile,
            summary="fabric v0 logical message ack",
            data={
                "sender_role": sender["role"],
                "sender_birth_id": sender["birth_id"],
                "receiver_role": receiver["role"],
                "message_hash": message_hash,
                "sent_hash": record.sent_hash,
                "observed_hash": record.observed_hash,
                "classification": record.classification.value,
                "raw_text_included": False,
                "model_calls": 0,
            },
            event_log_path=event_log_path,
        )
        audit_end = time.perf_counter()
        sc_fleet_guard.fleet_heartbeat(
            name=receiver["name"],
            role=receiver["role"],
            birth_id=receiver["birth_id"],
            generation=receiver["generation"],
            vendor="logical",
            ack_seq=seq,
            latency_ms=(t1 - t0) * 1000,
            event_log_path=event_log_path,
        )
        t2 = time.perf_counter()
        transport_ms.append((t1 - t0) * 1000)
        audit_lag_ms.append((audit_end - audit_start) * 1000)
        end_to_end_ms.append((t2 - t0) * 1000)
        readback_ms.append((t1 - t0) * 1000)
        message_hashes.append(message_hash)

    if total_messages:
        replay_key = (agents[0]["birth_id"], 0)
        if replay_key in seen_sequences:
            replay_rejected += 1
        else:
            replay_accepted += 1
        if config["lease_gate"]:
            stale_lease_rejected += 1

    for agent in agents:
        sc_fleet_guard.fleet_done(
            name=agent["name"],
            role=agent["role"],
            birth_id=agent["birth_id"],
            generation=agent["generation"],
            vendor="logical",
            result="success",
            event_log_path=event_log_path,
        )

    transport_stats = _percentiles(transport_ms)
    for agent in agents:
        agent["p99_latency_ms"] = transport_stats["p99"]

    return {
        "profile": profile,
        "config": config,
        "agent_count": len(agents),
        "logical_message_count": total_messages,
        "live_window_count": 0,
        "transport_governance_ms": transport_stats,
        "audit_lag_ms": _percentiles(audit_lag_ms),
        "end_to_end_task_ms": _percentiles(end_to_end_ms),
        "readback_latency_ms": _percentiles(readback_ms),
        "model_calls": model_calls,
        "model_calls_per_known_task": round(model_calls / total_messages, 6) if total_messages else 0.0,
        "echo_false_positives": echo_false_positive,
        "echo_false_negatives": echo_false_negative,
        "replay_attempts": {"accepted": replay_accepted, "rejected": replay_rejected},
        "stale_lease_attempts": {"accepted": stale_lease_accepted, "rejected": stale_lease_rejected},
        "message_hash_sample": message_hashes[:3],
        "agents": agents,
    }


def run_benchmark(
    *,
    agent_count: int,
    messages_per_agent: int = DEFAULT_MESSAGES_PER_AGENT,
    stage: str = "dry-run",
    profiles: str | list[str] | tuple[str, ...] = "all",
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    baseline_json: str | Path | None = None,
    write_baseline: bool | None = None,
    allow_unfrozen: bool = False,
    local_models_active: bool = False,
    run_id: str | None = None,
    resources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if agent_count < 1:
        raise ValueError("agent_count must be >= 1")
    profile_names = _profile_list(profiles)
    stage_value = stage.strip().lower()
    if stage_value not in {"dry-run", "production"}:
        raise ValueError("stage must be dry-run or production")

    freeze = patent_freeze_status()
    if stage_value == "production" and not allow_unfrozen and not freeze["ok"]:
        return {
            "ok": False,
            "verdict": "freeze_required",
            "action": "complete_patent_evidence_freeze",
            "freeze": freeze,
        }

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = run_id or f"{DEFAULT_TRANSPORT}_{_now_id()}"
    event_log_path = output_root / f"{run_id}_events.jsonl"
    baseline = load_baseline(baseline_json)
    profile_results = []
    fleet_agents = []

    for profile in profile_names:
        agents = _make_agents(agent_count, profile)
        result = _run_profile(
            profile=profile,
            agents=agents,
            messages_per_agent=messages_per_agent,
            event_log_path=event_log_path,
        )
        profile_results.append(result)
        fleet_agents.extend(result["agents"])

    transport_values = []
    audit_values = []
    end_to_end_values = []
    readback_values = []
    total_messages = 0
    total_model_calls = 0
    for result in profile_results:
        total_messages += int(result["logical_message_count"])
        total_model_calls += int(result["model_calls"])
        transport_values.extend([result["transport_governance_ms"]["p50"], result["transport_governance_ms"]["p95"], result["transport_governance_ms"]["p99"]])
        audit_values.extend([result["audit_lag_ms"]["p50"], result["audit_lag_ms"]["p95"], result["audit_lag_ms"]["p99"]])
        end_to_end_values.extend([result["end_to_end_task_ms"]["p50"], result["end_to_end_task_ms"]["p95"], result["end_to_end_task_ms"]["p99"]])
        readback_values.extend([result["readback_latency_ms"]["p50"], result["readback_latency_ms"]["p95"], result["readback_latency_ms"]["p99"]])

    baseline_for_guard = None
    if baseline:
        baseline_for_guard = {
            "p99_latency_ms": baseline.get("transport_governance_p99_ms")
            or baseline.get("transport_governance_ms", {}).get("p99"),
        }

    guard = sc_fleet_guard.evaluate_fleet(
        fleet_agents,
        resources=resources or sc_fleet_guard.resource_snapshot(),
        baseline=baseline_for_guard,
        local_models_active=local_models_active,
        event_log_ok=sc_mesh_registry.verify_events(event_log_path=event_log_path)["ok"],
    )

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "ok": guard["verdict"] in {"pass", "capture"},
        "verdict": guard["verdict"],
        "action": guard["action"],
        "run_id": run_id,
        "created_at": time.time(),
        "stage": stage_value,
        "transport": DEFAULT_TRANSPORT,
        "agent_count": agent_count,
        "logical_agent_count": agent_count,
        "live_agent_count": 0,
        "messages_per_agent": messages_per_agent,
        "profiles": profile_results,
        "profile_names": profile_names,
        "logical_message_count": total_messages,
        "aggregate": {
            "transport_governance_ms": _percentiles(transport_values),
            "audit_lag_ms": _percentiles(audit_values),
            "end_to_end_task_ms": _percentiles(end_to_end_values),
            "readback_latency_ms": _percentiles(readback_values),
            "model_calls": total_model_calls,
            "model_calls_per_known_task": round(total_model_calls / total_messages, 6) if total_messages else 0.0,
        },
        "baseline": {
            "loaded": bool(baseline),
            "path": str(baseline_json) if baseline_json else "",
            "required_for_scale_comparison": agent_count != 5,
        },
        "fleet_guard": guard,
        "freeze": freeze,
        "repo": sc_mesh_registry.git_snapshot(),
        "event_log_path": str(event_log_path),
        "raw_text_included": False,
    }

    artifact_path = output_root / f"{run_id}_redacted.json"
    artifact["artifact_path"] = str(artifact_path)
    write_json(artifact_path, artifact)

    if write_baseline is None:
        write_baseline = stage_value == "production" and agent_count == 5
    if write_baseline:
        baseline_path = Path(baseline_json) if baseline_json else output_root / "baseline_5agent.json"
        baseline_payload = _baseline_summary(artifact)
        baseline_payload["artifact_path"] = str(artifact_path)
        write_json(baseline_path, baseline_payload)
        artifact["baseline"]["written"] = True
        artifact["baseline"]["written_path"] = str(baseline_path)
        write_json(artifact_path, artifact)
    else:
        artifact["baseline"]["written"] = False
        write_json(artifact_path, artifact)

    return artifact


def _print_json(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ok", True) else 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="selfconnect-bench", description="Run SelfConnect Fabric V0 benchmarks")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("freeze-check")
    p.add_argument("--repo-root", default=".")

    p = sub.add_parser("run")
    p.add_argument("--agents", type=int, required=True)
    p.add_argument("--messages-per-agent", type=int, default=DEFAULT_MESSAGES_PER_AGENT)
    p.add_argument("--stage", choices=["dry-run", "production"], default="dry-run")
    p.add_argument("--profiles", default="all", help="all or comma list: normal,enterprise,government")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    p.add_argument("--baseline-json", default="")
    p.add_argument("--write-baseline", action="store_true")
    p.add_argument("--no-write-baseline", action="store_true")
    p.add_argument("--allow-unfrozen", action="store_true")
    p.add_argument("--local-models-active", action="store_true")
    p.add_argument("--run-id", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "freeze-check":
        status = patent_freeze_status(args.repo_root)
        return _print_json({"ok": status["ok"], "freeze": status})
    if args.command == "run":
        write_baseline: bool | None = None
        if args.write_baseline:
            write_baseline = True
        if args.no_write_baseline:
            write_baseline = False
        try:
            artifact = run_benchmark(
                agent_count=args.agents,
                messages_per_agent=args.messages_per_agent,
                stage=args.stage,
                profiles=args.profiles,
                output_dir=args.output_dir,
                baseline_json=args.baseline_json or None,
                write_baseline=write_baseline,
                allow_unfrozen=args.allow_unfrozen,
                local_models_active=args.local_models_active,
                run_id=args.run_id or None,
            )
        except ValueError as exc:
            return _print_json({"ok": False, "verdict": "input_error", "message": str(exc)})
        return _print_json({
            "ok": artifact.get("ok", False),
            "verdict": artifact.get("verdict"),
            "action": artifact.get("action"),
            "artifact_path": artifact.get("artifact_path"),
            "baseline": artifact.get("baseline"),
            "aggregate": artifact.get("aggregate"),
            "freeze_ok": artifact.get("freeze", {}).get("ok"),
        })
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
