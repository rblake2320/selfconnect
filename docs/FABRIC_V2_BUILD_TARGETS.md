# Fabric V2 Build Targets And Benchmark Gates

Last updated: 2026-06-20

Private engineering planning note. Not approved for public disclosure.

## Core Sequence

```text
File now on what is already proven.
Benchmark now against the current transport.
Then build Fabric V2 and prove the delta.
```

Do not build Fabric V2 first and measure afterward. The current transport needs
a baseline so the IOCP/named-pipe work can prove an actual improvement.

## Meaning Of "20 Agents"

Twenty agents is not a magic number and should not be framed as the only product
claim.

The real target is:

```text
More than the current live mesh size can operate without routing confusion,
unbounded queues, replay acceptance, evidence loss, or wrong-target writes.
```

Use profile levels:

| Profile | Purpose |
| --- | --- |
| `current-plus` | Current live mesh size plus one additional logical agent |
| `8-agent` | Small scale beyond the current coordination pattern |
| `12-agent` | Medium scale with slow consumers and migration |
| `20-agent-stress` | Stress profile for patent/product evidence, not a minimum requirement |

## Benchmark V0: Current Transport

Build the first benchmark against today's transport before refactoring:

- `WM_CHAR` / UIA for live terminal paths where safe;
- file registry and mesh event log for metadata;
- local logical/simulated agents for load that does not need visible terminals;
- current echo filter and target guard;
- current lease and birth/generation identity where available.

The goal is to discover the real baseline, not to make the current transport look
perfect.

Expected artifacts:

- `experiments/fabric_v2/n_agent_benchmark.py`
- `experiments/fabric_v2/results/current_transport_<timestamp>_redacted.json`
- `docs/FABRIC_V2_BENCHMARK_RESULTS.md`
- `docs/FABRIC_V0_BENCHMARK_SPEC.md`
- `docs/FABRIC_V2_STRESS_PROTOCOL.md`
- `docs/PATENT_EVIDENCE_FREEZE_2026-06-20.md`
- `docs/FLEET_AUTO_HALT_PROTOCOL.md`
- tests for metrics parsing and pass/fail gate evaluation.

The package command for the v0/current-transport harness is:

```powershell
selfconnect-bench freeze-check
selfconnect-bench run --agents 5 --stage production --profiles all
```

## Benchmark Metrics

Every run should emit JSON with:

- repo snapshot: branch, commit, dirty flag, ahead/behind;
- transport profile;
- agent count;
- logical message count;
- live window count;
- queue depth max and p95;
- enqueue latency p50/p95/p99;
- delivery latency p50/p95/p99;
- readback latency p50/p95/p99;
- audit/event-log lag p50/p95/p99;
- replay attempts accepted/rejected;
- stale lease attempts accepted/rejected;
- wrong-target attempts accepted/rejected;
- echo false positives and false negatives;
- dropped messages;
- timed-out messages;
- hung-consumer recovery time;
- crash/restart recovery result;
- token/model-call count where applicable.

## Initial Gates

These are initial engineering gates. The first current-transport baseline may
revise latency targets, but correctness gates should not be loosened.

| Gate | Current transport baseline | Fabric V2 target |
| --- | --- | --- |
| Wrong-target writes | `0` | `0` |
| Replay rejection | `100%` rejected | `100%` rejected |
| Stale/wrong lease rejection | `100%` rejected | `100%` rejected |
| Echo false response routing | `0` accepted as peer output | `0` accepted as peer output |
| Event-log integrity | `verify-events ok=true` | `verify-events ok=true` |
| Queue growth | bounded by configured limit or run fails | bounded by configured limit or run fails |
| Hung consumer | run continues; hung role marked degraded/blocked | run continues; bounded mailbox backpressure |
| Audit lag p95 | record baseline; fail if unbounded | `<250 ms` local event persistence |
| Delivery p95 | record baseline | `<150 ms` at `8-agent`; `<500 ms` at `20-agent-stress` |
| Delivery p99 | record baseline | `<1.5 s` at `20-agent-stress` |
| Model calls for replayable work | record baseline | deterministic replay path uses `0` model calls |

## Fabric V2 Target

Fabric V2 should add:

- SelfConnect Host Service;
- authenticated local IPC;
- Named Pipes with IOCP on Windows;
- bounded per-agent mailboxes;
- deadlines and cancellation;
- backpressure;
- session key establishment;
- sign-once/MAC-many frames;
- sequence-number replay protection;
- shared evidence store;
- shared blackboard/context capsules.

Win32 remains the discovery, identity, bootstrap, and semantic execution layer.
The high-rate mesh data plane moves to the Fabric V2 adapter.

## Patent And Competitor Boundary

Read these before turning Fabric V2 into claims or marketing:

- `docs/PATENT_DESIGN_AROUND_DEFENSE.md`
- `docs/LANCELOT_UAB_COMPETITIVE_DEFENSE.md`
- `docs/ARCHITECTURE_CONSENSUS_2026-06-20.md`

Fabric V2 should strengthen the SelfConnect lane without drifting into claims
others already publicly occupy. Do not claim:

- first generic desktop automation;
- first AI-to-AI desktop communication;
- first UIA app control;
- first adaptive cascade/channel selection;
- CAPTCHA bypass or anti-bot evasion.

Do claim and prove the harder-to-design-around SelfConnect chain:

- authority from OS or hardware-backed local identity;
- fail-closed live target-fact binding before actuation;
- surface-specific native channel routing;
- echo-filtered readback and delivery evidence;
- role freshness through birth ID, generation, lease, and target facts;
- optional MCP/API adapters that are not required for the local control path;
- execution hierarchy that uses deterministic replay before model reasoning;
- sign-once/MAC-many governed session traffic;
- benchmarked scale with bounded mailboxes, replay rejection, and evidence
  persistence.

The competitor response to assume: they will replace individual nouns. They may
replace named pipes with another IPC, `HWND` with an accessibility runtime ID,
HMAC with AEAD, UIA with DOM or AXUIElement, or local mailboxes with a broker.
The defense is to claim the invariant chain and disclose multiple species.

## Branching And Version Control

Use smart version control:

- Keep work off `master` until reviewed.
- Use `test/win32-hardening-v1` for the active integration branch unless a larger
  code sprint needs a topic branch.
- Suggested topic branches:
  - `bench/n-agent-baseline-2026-06-20`
  - `fabric/v2-named-pipe-iocp`
  - `fabric/v2-sign-once-mac-many`
- Commit docs/rationale separately from runtime implementation.
- Commit benchmark harness separately from Fabric V2 implementation.
- Every benchmark artifact must include the git snapshot already emitted by
  `selfconnect-mesh event`.
- Before and after meaningful work:

```powershell
selfconnect-mesh repo
selfconnect-mesh event --type task_started --role <role> --summary "<task>"
selfconnect-mesh event --type task_complete --role <role> --summary "<result>"
selfconnect-mesh verify-events
```

## Filing Boundary

Do not block provisional filing on Fabric V2.

File on what is already proven:

- OS-native channel routing by target surface;
- fail-closed target verification;
- UIA/browser control without CDP/WebDriver/MCP control path;
- echo-filtered readback;
- SID-bound role leases;
- local-model action source through governed native transport;
- tamper-evident mesh history;
- execution hierarchy;
- sign-once/MAC-many governed session pattern.

Use Fabric V2 benchmarks and IOCP implementation as continuation and dependent
claim material.
