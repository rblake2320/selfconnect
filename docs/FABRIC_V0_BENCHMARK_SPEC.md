# Fabric V0 Benchmark Spec

Private engineering planning note - not approved for public disclosure.

Fabric V0 measures the current SelfConnect transport before Fabric V2 changes
the data plane. The purpose is not to make the current transport look perfect.
The purpose is to create a defensible baseline so later Fabric V2 results prove
an actual delta.

## Hard Gate

Run the patent evidence freeze check before any shareable benchmark:

```powershell
selfconnect-bench freeze-check
```

Production benchmark runs fail closed unless the freeze packet is present or the
operator explicitly passes `--allow-unfrozen` for private local testing.

## Harness Command

```powershell
selfconnect-bench run --agents 5 --stage production --profiles all
selfconnect-bench run --agents 10 --stage production --profiles all --baseline-json experiments/fabric_v2/results/baseline_5agent.json
selfconnect-bench adversarial
```

The 5-agent production run writes:

```text
experiments/fabric_v2/results/baseline_5agent.json
```

Later rungs read that file so `p99 > 5x baseline` is an automated stop, not a
manual judgment.

Run `selfconnect-bench adversarial` before real-terminal ladders. It covers
fault injection, tamper detection, resource halt simulation, and 100/1000
messages-per-agent logical load.

## Latency Tracks

The harness records separate tracks:

| Track | Meaning |
| --- | --- |
| `transport_governance_ms` | message envelope, sequence/replay check, echo-filtered readback classification |
| `audit_lag_ms` | local tamper-evident mesh event persistence |
| `end_to_end_task_ms` | transport/governance plus audit/heartbeat path |
| `readback_latency_ms` | readback-classification timing |

Fabric V2 should be compared mainly on `transport_governance_ms`; model
inference time is intentionally excluded from that track.

## Required Metrics

Each artifact includes:

- repo snapshot;
- freeze status;
- transport profile;
- profile lane (`normal`, `enterprise`, `government`);
- agent count;
- logical and live agent count;
- message count;
- p50/p95/p99 per latency track;
- model calls per known task;
- replay accepted/rejected;
- stale lease accepted/rejected;
- echo false positives/negatives;
- fleet guard verdict;
- event log hash-chain verification result;
- redaction flag.

## Product Lanes

| Lane | Benchmark role |
| --- | --- |
| `normal` | fast day-to-day mesh; target guard and echo filter still on |
| `enterprise` | governed lease/audit posture without government fail-closed requirements |
| `government` | full-governance profile; WORM/TPM may report NA in v0 but the lane remains explicit |

Capturing all three lanes quantifies governance cost instead of pretending every
deployment has the same overhead.

## Model-Call Metric

The headline derived metric is:

```text
model_calls_per_known_task
```

Known replayable tasks should trend toward `0`. That metric supports both the
execution-hierarchy claim and the product unit-economics story.

## Output Schema

Artifacts are written as:

```text
experiments/fabric_v2/results/current_transport_<timestamp>_redacted.json
```

Top-level fields:

```json
{
  "schema_version": 1,
  "run_id": "current_transport_...",
  "stage": "production",
  "transport": "current_transport",
  "agent_count": 5,
  "logical_agent_count": 5,
  "live_agent_count": 0,
  "profile_names": ["normal", "enterprise", "government"],
  "aggregate": {},
  "profiles": [],
  "baseline": {},
  "fleet_guard": {},
  "freeze": {},
  "repo": {},
  "raw_text_included": false
}
```

Raw prompts, transcripts, private paths, and live terminal text must not be
included in benchmark artifacts.
