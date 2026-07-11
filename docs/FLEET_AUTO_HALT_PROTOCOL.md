# Fleet Auto-Halt Protocol

Last updated: 2026-06-20

Private benchmark safety protocol. Not approved for public disclosure.

## Purpose

`sc_fleet_guard.py` is the benchmark safety layer for staged SelfConnect fleet
runs. It belongs at the orchestrator/dashboard boundary, not inside individual
Codex, Claude, Gemini, or local-model sessions.

The default action is non-destructive:

```text
stop assigning new work -> freeze stage -> capture evidence -> report verdict
```

It does not kill terminals unless a future caller deliberately implements a
separate `--kill-on-hard-stop` cleanup path.

## Orchestrator Hooks

Call these from the component that spawns agents and observes ACKs:

```python
from sc_fleet_guard import fleet_register, fleet_heartbeat, fleet_done

fleet_register(name=agent_id, task=task_desc, pid=pid, hwnd=hwnd,
               role=role, birth_id=birth_id, generation=generation,
               vendor="codex")

fleet_heartbeat(name=agent_id, status="working", note=last_ack_text,
                ack_seq=ack_seq, latency_ms=latency_ms,
                role=role, birth_id=birth_id, generation=generation)

fleet_done(name=agent_id, result="success", reason="stage complete",
           final_event_hash=head_hash, role=role, birth_id=birth_id,
           generation=generation)
```

If a separate `mesh_wire.py` exists in another repo, wire these calls there. In
this core SDK repo, the same hooks are available to the benchmark/orchestrator
layer and any dashboard adapter.

## CLI

```powershell
selfconnect-fleet resources
selfconnect-fleet guard --state-json .\stage_state.json
selfconnect-fleet register --name codex-1 --role codex-1 --birth-id codex-1-a --vendor codex
selfconnect-fleet heartbeat --name codex-1 --ack-seq 12 --latency-ms 48.2
selfconnect-fleet done --name codex-1 --result success
```

The guard command reads a JSON state file shaped like:

```json
{
  "run_active": true,
  "local_models_active": false,
  "event_log_ok": true,
  "resources": {
    "ram_free_mb": 72000,
    "gpu": {"vram_free_mb": 28000}
  },
  "baseline": {"p99_latency_ms": 200},
  "agents": [
    {"name": "codex-1", "missed_acks": 0, "p99_latency_ms": 180}
  ]
}
```

## Verdicts

| Verdict | Meaning | Default action |
| --- | --- | --- |
| `pass` | Run can continue | continue |
| `capture` | First anomaly; evidence should be captured | capture and continue |
| `halt_recommended` | Resource floor crossed | stop assigning and capture |
| `hard_stop` | Correctness or protocol failure | stop and capture |

## Initial Guard Rules

- RAM free below `25 GB` while run active -> `halt_recommended`.
- VRAM free below `6 GB` while local models active -> `halt_recommended`.
- First missed ACK -> `capture`.
- Two missed ACKs from one agent -> agent report becomes `blocked`.
- Three blocked agents -> `hard_stop`.
- Two local narration violations -> `hard_stop`.
- Approval block over `90s` -> `hard_stop`.
- Wrong-window guard failure -> `hard_stop`.
- Wrong sender/nonce/hash accepted -> `hard_stop`.
- Replay accepted -> `hard_stop`.
- Stale lease accepted -> `hard_stop`.
- P99 latency over `5x` the 5-agent baseline -> `hard_stop`.
- Queue depth over configured bound -> `hard_stop`.
- Mesh event log verification failure -> `hard_stop`.

## Evidence To Capture On First Anomaly

- affected agent role, birth ID, generation, HWND, PID, and title;
- UIA readback or capture of affected terminal;
- mesh registry snapshot;
- `mesh_events.jsonl` head hash;
- RAM/CPU/GPU resource snapshot;
- ACK nonce/sequence/latency metadata;
- git snapshot from the event log.

## Relationship To Normal Mode

This guard is for benchmark and fleet runs. Normal everyday SelfConnect remains
fast and low-friction. The always-on safety invariants are still target guard,
birth IDs, echo-filtered readback, and mesh history.
