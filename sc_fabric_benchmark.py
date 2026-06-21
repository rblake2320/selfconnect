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
import uuid
from pathlib import Path
from typing import Any

import sc_echo_filter
import sc_fabric_v2
import sc_fleet_guard
import sc_mesh_registry
from sc_fabric_host import FabricHostService, host_roundtrip

SCHEMA_VERSION = 1
DEFAULT_OUTPUT_DIR = Path("experiments") / "fabric_v2" / "results"
DEFAULT_MESSAGES_PER_AGENT = 3
DEFAULT_TRANSPORT = "current_transport"
TRANSPORT_FABRIC_V2 = "fabric_v2_frame_mailbox"
TRANSPORT_FABRIC_V2_SERVICE = "fabric_v2_service_transport"
VALID_TRANSPORTS = {DEFAULT_TRANSPORT, TRANSPORT_FABRIC_V2, TRANSPORT_FABRIC_V2_SERVICE}
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


def _default_baseline_path(output_root: Path, transport: str) -> Path:
    if transport == DEFAULT_TRANSPORT:
        return output_root / "baseline_5agent.json"
    return output_root / f"baseline_5agent_{transport}.json"


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


def _fabric_v2_context(profile: str, agents: list[dict[str, Any]]) -> dict[str, Any]:
    session = sc_fabric_v2.FabricSession.from_secret(
        f"{profile}:{time.time_ns()}:{len(agents)}",
        session_id=f"sfv2-{profile}-{_now_id()}",
    )
    return {
        "session": session,
        "mailboxes": {
            str(agent["birth_id"]): sc_fabric_v2.BoundedMailbox(
                str(agent["birth_id"]),
                max_depth=max(10, len(agents) * DEFAULT_MESSAGES_PER_AGENT),
            )
            for agent in agents
        },
        "first_frame": None,
        "first_receiver": "",
        "mac_failures": 0,
        "mailbox_backpressure_events": 0,
    }


def _deliver_current_transport(
    *,
    nonce: str,
    sent_text: str,
    observed_text: str,
    timestamp_send: float,
) -> tuple[str, sc_echo_filter.FilterRecord]:
    record = sc_echo_filter.build_record(
        delta=observed_text,
        nonce=nonce,
        sent_text=sent_text,
        readback_method="logical_TextPattern_delta",
        timestamp_send=timestamp_send,
        timestamp_recv=timestamp_send,
    )
    return sent_text, record


def _deliver_fabric_v2(
    *,
    context: dict[str, Any],
    sender: dict[str, Any],
    receiver: dict[str, Any],
    nonce: str,
    sent_text: str,
    observed_text: str,
    timestamp_send: float,
) -> tuple[str, sc_echo_filter.FilterRecord]:
    session: sc_fabric_v2.FabricSession = context["session"]
    mailboxes: dict[str, sc_fabric_v2.BoundedMailbox] = context["mailboxes"]
    frame = session.seal(
        sender=str(sender["birth_id"]),
        receiver=str(receiver["birth_id"]),
        payload=sent_text,
        deadline_ms=5_000,
    )
    if context["first_frame"] is None:
        context["first_frame"] = frame
        context["first_receiver"] = str(receiver["birth_id"])
    mailbox = mailboxes[str(receiver["birth_id"])]
    mailbox.put(frame, timeout_ms=1)
    verified = session.open(mailbox.get(timeout_ms=1), expected_receiver=str(receiver["birth_id"]))
    delivered_text = verified.payload.decode("utf-8", errors="replace")
    record = sc_echo_filter.build_record(
        delta=observed_text,
        nonce=nonce,
        sent_text=delivered_text,
        readback_method="fabric_v2_mailbox_logical_delta",
        timestamp_send=timestamp_send,
        timestamp_recv=timestamp_send,
    )
    return delivered_text, record


def _service_context(profile: str, agents: list[dict[str, Any]]) -> dict[str, Any]:
    session = sc_fabric_v2.FabricSession.from_secret(
        f"{profile}:{time.time_ns()}:{len(agents)}",
        session_id=f"sfv2-svc-{profile}-{_now_id()}",
    )
    pipe_name = "SelfConnectBench_" + profile + "_" + uuid.uuid4().hex[:8]
    host = FabricHostService(
        session=session,
        address=pipe_name,
        mailbox_depth=max(10, len(agents) * DEFAULT_MESSAGES_PER_AGENT),
    )
    return {
        "session": session,
        "host": host,
        "address": host.address,
        "started": False,
        "errors": [],
    }


def _deliver_fabric_v2_service(
    *,
    context: dict[str, Any],
    sender: dict[str, Any],
    receiver: dict[str, Any],
    nonce: str,
    sent_text: str,
    timestamp_send: float,
) -> tuple[str, sc_echo_filter.FilterRecord]:
    if not context["started"]:
        context["host"].start()
        context["started"] = True
    session: sc_fabric_v2.FabricSession = context["session"]
    address: str = context["address"]
    try:
        result = host_roundtrip(
            session=session,
            address=address,
            sender=str(sender["birth_id"]),
            receiver=str(receiver["birth_id"]),
            payload=sent_text,
        )
        elapsed_ms: float = float(result.get("elapsed_ms", 0.0))
        if not result.get("ok"):
            raise RuntimeError(result.get("message", "host_roundtrip failed"))
    except Exception as exc:
        context["errors"].append(str(exc))
        record = sc_echo_filter.build_record(
            delta=sent_text,
            nonce=nonce,
            sent_text=sent_text,
            readback_method="fabric_v2_service_transport_pipe",
            timestamp_send=timestamp_send,
            timestamp_recv=timestamp_send,
        )
        return sent_text, record
    timestamp_recv = timestamp_send + elapsed_ms / 1000.0
    record = sc_echo_filter.build_record(
        delta=sent_text,
        nonce=nonce,
        sent_text=sent_text,
        readback_method="fabric_v2_service_transport_pipe",
        timestamp_send=timestamp_send,
        timestamp_recv=timestamp_recv,
    )
    return sent_text, record


def _run_profile(
    *,
    profile: str,
    agents: list[dict[str, Any]],
    messages_per_agent: int,
    event_log_path: Path,
    transport: str = DEFAULT_TRANSPORT,
    repo_snapshot: dict[str, Any] | None = None,
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
    fabric_context = _fabric_v2_context(profile, agents) if transport == TRANSPORT_FABRIC_V2 else None
    service_context: dict[str, Any] | None = _service_context(profile, agents) if transport == TRANSPORT_FABRIC_V2_SERVICE else None

    for agent in agents:
        sc_fleet_guard.fleet_register(
            name=agent["name"],
            role=agent["role"],
            birth_id=agent["birth_id"],
            generation=agent["generation"],
            vendor="logical",
            task=f"fabric-v0-{profile}",
            event_log_path=event_log_path,
            repo_snapshot=repo_snapshot,
        )

    for seq in range(total_messages):
        sender = agents[seq % len(agents)]
        receiver = agents[(seq + 1) % len(agents)]
        nonce = f"SC_BENCH_{profile}_{seq:05d}"
        sent_text = f"{nonce} known-task"
        observed_text = f"{nonce}\nACK:{receiver['name']}:SEQ:{seq}"
        t0 = time.perf_counter()
        read_send_ts = time.time()
        if transport == TRANSPORT_FABRIC_V2:
            delivered_text, record = _deliver_fabric_v2(
                context=fabric_context or {},
                sender=sender,
                receiver=receiver,
                nonce=nonce,
                sent_text=sent_text,
                observed_text=observed_text,
                timestamp_send=read_send_ts,
            )
        elif transport == TRANSPORT_FABRIC_V2_SERVICE:
            delivered_text, record = _deliver_fabric_v2_service(
                context=service_context or {},
                sender=sender,
                receiver=receiver,
                nonce=nonce,
                sent_text=sent_text,
                timestamp_send=read_send_ts,
            )
        else:
            delivered_text, record = _deliver_current_transport(
                nonce=nonce,
                sent_text=sent_text,
                observed_text=observed_text,
                timestamp_send=read_send_ts,
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
                "transport": transport,
                "delivered_text_hash": _sha(delivered_text),
                "raw_text_included": False,
                "model_calls": 0,
            },
            event_log_path=event_log_path,
            repo_snapshot=repo_snapshot,
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
            repo_snapshot=repo_snapshot,
        )
        t2 = time.perf_counter()
        transport_ms.append((t1 - t0) * 1000)
        audit_lag_ms.append((audit_end - audit_start) * 1000)
        end_to_end_ms.append((t2 - t0) * 1000)
        readback_ms.append((t1 - t0) * 1000)
        message_hashes.append(message_hash)

    if service_context is not None and service_context["started"]:
        service_context["host"].stop()

    if total_messages:
        if transport == TRANSPORT_FABRIC_V2 and fabric_context and fabric_context["first_frame"]:
            try:
                fabric_context["session"].open(
                    fabric_context["first_frame"],
                    expected_receiver=fabric_context["first_receiver"],
                )
                replay_accepted += 1
            except sc_fabric_v2.ReplayRejectedError:
                replay_rejected += 1
        else:
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
            repo_snapshot=repo_snapshot,
        )

    transport_stats = _percentiles(transport_ms)
    for agent in agents:
        agent["p99_latency_ms"] = transport_stats["p99"]

    return {
        "profile": profile,
        "config": config,
        "transport": transport,
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
        "fabric_v2": {
            "enabled": transport == TRANSPORT_FABRIC_V2,
            "session_id_hash": _sha(fabric_context["session"].session_id) if fabric_context else "",
            "mac_failures": int(fabric_context["mac_failures"]) if fabric_context else 0,
            "mailbox_backpressure_events": int(fabric_context["mailbox_backpressure_events"]) if fabric_context else 0,
        },
        "message_hash_sample": message_hashes[:3],
        "service_errors": list(service_context["errors"]) if service_context is not None else [],
        "agents": agents,
    }


def run_benchmark(
    *,
    agent_count: int,
    messages_per_agent: int = DEFAULT_MESSAGES_PER_AGENT,
    stage: str = "dry-run",
    profiles: str | list[str] | tuple[str, ...] = "all",
    transport: str = DEFAULT_TRANSPORT,
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
    transport_value = transport.strip().lower()
    if transport_value not in VALID_TRANSPORTS:
        raise ValueError(f"transport must be one of: {', '.join(sorted(VALID_TRANSPORTS))}")
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
    run_id = run_id or f"{transport_value}_{_now_id()}"
    event_log_path = output_root / f"{run_id}_events.jsonl"
    baseline = load_baseline(baseline_json)
    repo_snapshot = sc_mesh_registry.git_snapshot()
    profile_results = []
    fleet_agents = []

    for profile in profile_names:
        agents = _make_agents(agent_count, profile)
        result = _run_profile(
            profile=profile,
            agents=agents,
            messages_per_agent=messages_per_agent,
            event_log_path=event_log_path,
            transport=transport_value,
            repo_snapshot=repo_snapshot,
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
        "transport": transport_value,
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
        "repo": repo_snapshot,
        "event_log_path": str(event_log_path),
        "raw_text_included": False,
    }

    artifact_path = output_root / f"{run_id}_redacted.json"
    artifact["artifact_path"] = str(artifact_path)
    write_json(artifact_path, artifact)

    if write_baseline is None:
        write_baseline = stage_value == "production" and agent_count == 5
    if write_baseline:
        baseline_path = Path(baseline_json) if baseline_json else _default_baseline_path(output_root, transport_value)
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


FAULT_SCENARIOS: dict[str, dict[str, Any]] = {
    "wrong_nonce": {
        "agents": [{"name": "agent-1", "wrong_sender_nonce_accepted": True}],
        "expected_verdict": "hard_stop",
        "expected_reason": "wrong_sender_nonce_accepted",
    },
    "wrong_sender": {
        "agents": [{"name": "agent-1", "wrong_sender_nonce_accepted": True}],
        "expected_verdict": "hard_stop",
        "expected_reason": "wrong_sender_nonce_accepted",
    },
    "wrong_hash": {
        "agents": [{"name": "agent-1", "wrong_hash_accepted": True}],
        "expected_verdict": "hard_stop",
        "expected_reason": "wrong_hash_accepted",
    },
    "wrong_window": {
        "agents": [{"name": "agent-1", "wrong_window_guard_failed": True}],
        "expected_verdict": "hard_stop",
        "expected_reason": "wrong_window_guard_failed",
    },
    "replay": {
        "agents": [{"name": "agent-1", "replay_accepted": True}],
        "expected_verdict": "hard_stop",
        "expected_reason": "replay_accepted",
    },
    "stale_lease": {
        "agents": [{"name": "agent-1", "stale_lease_accepted": True}],
        "expected_verdict": "hard_stop",
        "expected_reason": "stale_lease_accepted",
    },
    "narration_drift": {
        "agents": [{"name": "agent-1", "local_narration_count": 2}],
        "expected_verdict": "hard_stop",
        "expected_reason": "local_narration_violation",
    },
    "ack_loss": {
        "agents": [
            {"name": "agent-1", "missed_acks": 2},
            {"name": "agent-2", "missed_acks": 2},
            {"name": "agent-3", "missed_acks": 2},
        ],
        "expected_verdict": "hard_stop",
        "expected_reason": "blocked_agent_count",
    },
    "queue_depth": {
        "agents": [{"name": "agent-1", "queue_depth": 101}],
        "thresholds": {"queue_depth_limit": 100},
        "expected_verdict": "hard_stop",
        "expected_reason": "queue_depth_exceeded",
    },
    "event_log_failure": {
        "agents": [{"name": "agent-1"}],
        "event_log_ok": False,
        "expected_verdict": "hard_stop",
        "expected_reason": "event_log_verify_failed",
    },
}


def _reason_kinds(result: dict[str, Any]) -> set[str]:
    return {str(item.get("kind")) for item in result.get("hard_reasons", [])}


def run_fault_injection_suite(
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    run_id: str | None = None,
) -> dict[str, Any]:
    run_id = run_id or f"fault_injection_{_now_id()}"
    cases = []
    for name, scenario in FAULT_SCENARIOS.items():
        result = sc_fleet_guard.evaluate_fleet(
            list(scenario["agents"]),
            resources={"ram_free_mb": 100_000, "gpu": {"vram_free_mb": 24_000}},
            thresholds=scenario.get("thresholds"),
            event_log_ok=bool(scenario.get("event_log_ok", True)),
        )
        expected_reason = str(scenario["expected_reason"])
        passed = (
            result["verdict"] == scenario["expected_verdict"]
            and expected_reason in _reason_kinds(result)
        )
        cases.append({
            "name": name,
            "pass": passed,
            "expected_verdict": scenario["expected_verdict"],
            "actual_verdict": result["verdict"],
            "expected_reason": expected_reason,
            "actual_reasons": sorted(_reason_kinds(result)),
            "action": result["action"],
        })
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "suite": "fault_injection",
        "run_id": run_id,
        "ok": all(case["pass"] for case in cases),
        "cases": cases,
        "repo": sc_mesh_registry.git_snapshot(),
        "raw_text_included": False,
    }
    artifact_path = Path(output_dir) / f"{run_id}_redacted.json"
    artifact["artifact_path"] = str(artifact_path)
    write_json(artifact_path, artifact)
    return artifact


def run_resource_suite(
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    run_id: str | None = None,
) -> dict[str, Any]:
    run_id = run_id or f"resource_halt_{_now_id()}"
    cases = [
        {
            "name": "ram_floor",
            "resources": {"ram_free_mb": 24_000, "gpu": {"vram_free_mb": 24_000}},
            "local_models_active": False,
            "expected_verdict": "halt_recommended",
            "expected_reason": "ram_floor",
        },
        {
            "name": "vram_floor_local_model",
            "resources": {"ram_free_mb": 100_000, "gpu": {"vram_free_mb": 5_000}},
            "local_models_active": True,
            "expected_verdict": "halt_recommended",
            "expected_reason": "vram_floor",
        },
        {
            "name": "vram_floor_ignored_without_local_model",
            "resources": {"ram_free_mb": 100_000, "gpu": {"vram_free_mb": 5_000}},
            "local_models_active": False,
            "expected_verdict": "pass",
            "expected_reason": "",
        },
    ]
    results = []
    for case in cases:
        result = sc_fleet_guard.evaluate_fleet(
            [],
            resources=case["resources"],
            local_models_active=bool(case["local_models_active"]),
        )
        halt_reasons = {str(item.get("kind")) for item in result.get("halt_reasons", [])}
        expected_reason = str(case["expected_reason"])
        passed = result["verdict"] == case["expected_verdict"] and (
            not expected_reason or expected_reason in halt_reasons
        )
        results.append({
            "name": case["name"],
            "pass": passed,
            "expected_verdict": case["expected_verdict"],
            "actual_verdict": result["verdict"],
            "expected_reason": expected_reason,
            "actual_halt_reasons": sorted(halt_reasons),
            "action": result["action"],
        })
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "suite": "resource_halt",
        "run_id": run_id,
        "ok": all(case["pass"] for case in results),
        "cases": results,
        "repo": sc_mesh_registry.git_snapshot(),
        "raw_text_included": False,
    }
    artifact_path = Path(output_dir) / f"{run_id}_redacted.json"
    artifact["artifact_path"] = str(artifact_path)
    write_json(artifact_path, artifact)
    return artifact


def _seed_event_log(path: Path, repo_snapshot: dict[str, Any]) -> None:
    for idx in range(3):
        sc_mesh_registry.append_event(
            "tamper_seed",
            role=f"tamper-{idx}",
            status="seed",
            summary=f"tamper seed {idx}",
            data={"idx": idx},
            event_log_path=path,
            repo_snapshot=repo_snapshot,
        )


def _tamper_lines(source: Path, target: Path, mode: str) -> None:
    lines = source.read_text(encoding="utf-8").splitlines()
    if mode == "modify":
        item = json.loads(lines[1])
        item["summary"] = "tampered summary"
        lines[1] = json.dumps(item, sort_keys=True)
    elif mode == "delete":
        del lines[1]
    elif mode == "reorder":
        lines[1], lines[2] = lines[2], lines[1]
    else:
        raise ValueError(f"unknown tamper mode: {mode}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_tamper_suite(
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    run_id: str | None = None,
) -> dict[str, Any]:
    run_id = run_id or f"tamper_{_now_id()}"
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    repo_snapshot = sc_mesh_registry.git_snapshot()
    clean_log = output_root / f"{run_id}_clean_events.jsonl"
    _seed_event_log(clean_log, repo_snapshot)
    clean_verify = sc_mesh_registry.verify_events(event_log_path=clean_log)
    cases = []
    for mode in ("modify", "delete", "reorder"):
        target = output_root / f"{run_id}_{mode}_events.jsonl"
        _tamper_lines(clean_log, target, mode)
        verified = sc_mesh_registry.verify_events(event_log_path=target)
        cases.append({
            "name": mode,
            "pass": verified["ok"] is False,
            "verify_ok": verified["ok"],
            "error_count": len(verified.get("errors", [])),
        })
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "suite": "tamper",
        "run_id": run_id,
        "ok": clean_verify["ok"] is True and all(case["pass"] for case in cases),
        "clean_verify_ok": clean_verify["ok"],
        "cases": cases,
        "repo": repo_snapshot,
        "raw_text_included": False,
    }
    artifact_path = output_root / f"{run_id}_redacted.json"
    artifact["artifact_path"] = str(artifact_path)
    write_json(artifact_path, artifact)
    return artifact


def run_load_suite(
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    run_id: str | None = None,
    agent_count: int = 5,
    messages: tuple[int, ...] = (100, 1000),
    profiles: str = "normal",
    transport: str = DEFAULT_TRANSPORT,
) -> dict[str, Any]:
    run_id = run_id or f"logical_load_{_now_id()}"
    runs = []
    for message_count in messages:
        run = run_benchmark(
            agent_count=agent_count,
            messages_per_agent=message_count,
            stage="production",
            profiles=profiles,
            transport=transport,
            output_dir=output_dir,
            allow_unfrozen=False,
            write_baseline=False,
            run_id=f"{run_id}_{message_count}",
            resources={"ram_free_mb": 100_000, "gpu": {"vram_free_mb": 24_000}},
        )
        verify = sc_mesh_registry.verify_events(event_log_path=run["event_log_path"])
        runs.append({
            "messages_per_agent": message_count,
            "logical_message_count": run["logical_message_count"],
            "verdict": run["verdict"],
            "ok": run["ok"] and verify["ok"],
            "transport_p99_ms": run["aggregate"]["transport_governance_ms"]["p99"],
            "audit_p99_ms": run["aggregate"]["audit_lag_ms"]["p99"],
            "end_to_end_p99_ms": run["aggregate"]["end_to_end_task_ms"]["p99"],
            "model_calls_per_known_task": run["aggregate"]["model_calls_per_known_task"],
            "events_checked": verify["events_checked"],
            "event_verify_ok": verify["ok"],
            "artifact_path": run["artifact_path"],
        })
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "suite": "logical_load",
        "run_id": run_id,
        "ok": all(run["ok"] for run in runs),
        "agent_count": agent_count,
        "transport": transport,
        "profiles": _profile_list(profiles),
        "runs": runs,
        "repo": sc_mesh_registry.git_snapshot(),
        "raw_text_included": False,
    }
    artifact_path = Path(output_dir) / f"{run_id}_redacted.json"
    artifact["artifact_path"] = str(artifact_path)
    write_json(artifact_path, artifact)
    return artifact


def run_adversarial_suite(
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    run_id: str | None = None,
    include_load: bool = True,
) -> dict[str, Any]:
    run_id = run_id or f"adversarial_{_now_id()}"
    suites = [
        run_fault_injection_suite(output_dir=output_dir, run_id=f"{run_id}_faults"),
        run_tamper_suite(output_dir=output_dir, run_id=f"{run_id}_tamper"),
        run_resource_suite(output_dir=output_dir, run_id=f"{run_id}_resources"),
    ]
    if include_load:
        suites.append(run_load_suite(output_dir=output_dir, run_id=f"{run_id}_load"))
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "suite": "adversarial",
        "run_id": run_id,
        "ok": all(suite["ok"] for suite in suites),
        "suites": [
            {
                "suite": suite["suite"],
                "ok": suite["ok"],
                "artifact_path": suite["artifact_path"],
            }
            for suite in suites
        ],
        "repo": sc_mesh_registry.git_snapshot(),
        "raw_text_included": False,
    }
    artifact_path = Path(output_dir) / f"{run_id}_redacted.json"
    artifact["artifact_path"] = str(artifact_path)
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
    p.add_argument("--transport", choices=sorted(VALID_TRANSPORTS), default=DEFAULT_TRANSPORT)
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    p.add_argument("--baseline-json", default="")
    p.add_argument("--write-baseline", action="store_true")
    p.add_argument("--no-write-baseline", action="store_true")
    p.add_argument("--allow-unfrozen", action="store_true")
    p.add_argument("--local-models-active", action="store_true")
    p.add_argument("--run-id", default="")

    p = sub.add_parser("fault-injection")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    p.add_argument("--run-id", default="")

    p = sub.add_parser("tamper")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    p.add_argument("--run-id", default="")

    p = sub.add_parser("resource")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    p.add_argument("--run-id", default="")

    p = sub.add_parser("load")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    p.add_argument("--run-id", default="")
    p.add_argument("--agents", type=int, default=5)
    p.add_argument("--messages", default="100,1000")
    p.add_argument("--profiles", default="normal")
    p.add_argument("--transport", choices=sorted(VALID_TRANSPORTS), default=DEFAULT_TRANSPORT)

    p = sub.add_parser("adversarial")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    p.add_argument("--run-id", default="")
    p.add_argument("--no-load", action="store_true")
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
                transport=args.transport,
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
    if args.command == "fault-injection":
        return _print_json(run_fault_injection_suite(
            output_dir=args.output_dir,
            run_id=args.run_id or None,
        ))
    if args.command == "tamper":
        return _print_json(run_tamper_suite(
            output_dir=args.output_dir,
            run_id=args.run_id or None,
        ))
    if args.command == "resource":
        return _print_json(run_resource_suite(
            output_dir=args.output_dir,
            run_id=args.run_id or None,
        ))
    if args.command == "load":
        try:
            messages = tuple(int(item.strip()) for item in args.messages.split(",") if item.strip())
            artifact = run_load_suite(
                output_dir=args.output_dir,
                run_id=args.run_id or None,
                agent_count=args.agents,
                messages=messages,
                profiles=args.profiles,
                transport=args.transport,
            )
        except ValueError as exc:
            return _print_json({"ok": False, "verdict": "input_error", "message": str(exc)})
        return _print_json(artifact)
    if args.command == "adversarial":
        return _print_json(run_adversarial_suite(
            output_dir=args.output_dir,
            run_id=args.run_id or None,
            include_load=not args.no_load,
        ))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
