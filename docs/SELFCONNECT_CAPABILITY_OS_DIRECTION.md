# SelfConnect Capability OS Direction

Status: active engineering direction  
Branch: `feature/capability-kernel-v1`  
Started: 2026-07-25

## North star

SelfConnect will be a model-independent capability operating system for AI
agents on Windows:

> A model expresses intent. SelfConnect discovers the smallest relevant
> capability set, supplies fresh source-attributed context, executes only under
> explicit authority, independently verifies real state change, preserves
> evidence and task continuity, and converts proven procedures into reviewed
> reusable skills.

The model is the reasoning component. SelfConnect owns identity, capabilities,
permissions, execution, perception, memory hygiene, verification, continuity,
and evidence.

## Novelty position

This document is an engineering/IP direction, not a legal novelty opinion.

### Established patterns we use but do not claim broadly

- progressive skill and tool discovery;
- MCP and CLI adapters;
- permission brokers and least privilege;
- task DAGs and checkpoints;
- tool-result verification;
- hash-linked audit records;
- model routing;
- agent memory and retrieval;
- GUI automation and OCR individually.

These are important implementation building blocks, but they have substantial
published and patent prior art.

### Distinctive SelfConnect composition to preserve and prove

1. Model-independent AI interoperability through existing operating-system
   application surfaces without requiring participating agents to share an API.
2. Binding a logical mesh role, birth identity, and generation to a currently
   verified HWND, PID, executable, class, and title before interaction.
3. A user-visible UI/terminal conversation plane separated from an
   identity-sensitive routing, lease, health, and evidence control plane.
4. Semantic reply-delta verification that distinguishes outbound echo, stale
   history, new peer output, and wrong/stale windows.
5. Role continuity and migration across process, terminal, context, and model
   replacement while preserving explicit identity boundaries.
6. Capability execution bound to verified OS targets, immutable authority,
   independent state observation, and evidence contracts.
7. A channel-selection waterfall across UIA events/text, Win32 text, capture,
   OCR/vision, guarded terminal input, and sidecar transports.

Any IP work should focus on these narrow mechanisms and their composition, not
generic claims that an AI discovers or invokes tools.

## Architectural invariants

These rules are release blockers:

1. Models never receive raw authority merely because a skill is visible.
2. Skill manifests cannot contain executable imports, commands, credentials, or
   arbitrary endpoints.
3. Only trusted host code registers execution adapters.
4. Authority is fixed when a task/runtime starts and cannot be expanded by the
   model.
5. Mutation gates remain independently controlled.
6. OS input requires current target identity verification.
7. OCR, window text, webpages, files, and tool output are untrusted data.
8. “Done” requires verifier evidence, not model assertion.
9. Task and world state record source, time, and freshness.
10. Generated skills remain shadow-only until replay, security review, and
    approval succeed.
11. Default SelfConnect behavior remains unchanged behind feature flags until a
    release gate is passed.
12. Every externally stated capability is tied to a reproducible proof artifact.

## Target architecture

```text
User / peer / scheduler
          |
          v
Intent and task specification
          |
          v
Capability router  <---->  Skill registry and provenance
          |
          +--------> World state and relevant episodic memory
          |
          v
Durable task graph and completion contracts
          |
          v
Immutable authority and approval policy
          |
          v
Capability broker
  | CLI | MCP | Win32/UIA | mesh | browser | filesystem | specialist model |
          |
          v
Independent verifier and state transition observer
          |
          v
Evidence chain, checkpoint, and reviewed skill candidate
```

## Workstreams

### A. Capability Kernel

- strict content-addressed manifests;
- semantic discovery with one disclosure layer by default;
- immutable authority;
- trusted adapters;
- input/output validation;
- verification-gated execution;
- hash-linked evidence.

V1 status: implemented and live-proven.

### B. World state

- structured observations keyed by resource identity;
- source, timestamp, confidence, and expiration on every observation;
- current/stale distinction;
- change feed rather than repeated full rediscovery;
- no raw transcript as authoritative state;
- separate sensitive values from model-visible summaries.

### C. Durable task engine

- dependency-aware task graph;
- retries and bounded repair;
- explicit blocked/failed/cancelled states;
- task budgets and deadlines;
- crash/context/process resume;
- verifier-defined completion.

V1 checkpointing, completion predicates, budgets, deadlines, owner-bound
cancellation/retry, and successor recovery are implemented. Event-driven
scheduling remains in the active-perception workstream.

### D. Perception and action

Observation waterfall:

1. UIA event subscription;
2. UIA text/property read;
3. child-window/Win32 text;
4. PrintWindow or bounded screen capture;
5. OCR;
6. local visual specialist.

Action selection:

1. native application/API adapter;
2. MCP adapter;
3. UIA Value/Invoke;
4. guarded Win32 terminal input;
5. foreground input only under explicit supervised policy.

Every action must name its target identity and expected observable transition.

### E. Specialist routing

- Qwen 3.6 27B: primary local planning and tool selection;
- local VLM: bounded target-window interpretation;
- embedding model: retrieval and discovery;
- deterministic code: calculation, parsing, validation;
- cloud or mesh specialist: explicitly delegated hard tasks;
- independent critic/verifier: only where deterministic verification is not
  available.

Routing must consider capability fit, VRAM, latency, privacy, authority, and
verification cost. It must not create uncontrolled group chat.

### F. MCP and external capability adapters

- enumerate servers and tools through trusted configuration;
- convert tools to strict manifests;
- keep credentials server-side;
- bind each tool to permissions, provenance, timeout, and verification;
- expose only selected tools to the model;
- quarantine changed schemas until reviewed.

Threat-model gate: MCP implementation does not advance until injection
hardening, completion-integrity predicates, and full task recovery are proven.
External schemas, descriptions, and results are untrusted planner data.

### G. Experience-to-skill compiler

Successful traces may produce shadow skill candidates:

1. remove secrets and machine-specific identities;
2. infer inputs, outputs, permissions, and preconditions;
3. attach deterministic verifiers;
4. replay in an owned sandbox;
5. run injection, argument-smuggling, and privilege-escalation tests;
6. compare multiple runs;
7. require approval;
8. sign/version and publish.

The model cannot promote its own skill.

### H. Simulation and rollback

- preflight target and permission checks;
- dry-run plan and affected-resource inventory;
- temporary Git worktree or sandbox for file/code changes;
- owned test windows for UI workflows;
- rollback procedure before irreversible actions;
- explicit human checkpoint for protected or high-impact states.

### I. Active perception and scheduling

- wake on UIA, process, service, file, mesh, and resource changes;
- deduplicate and debounce events;
- update world state before waking a model;
- heartbeats for long-running tasks;
- resource-aware queuing and GPU admission control;
- no continuous model polling when deterministic events suffice.

### J. Evaluation and governance

Measure more than final answers:

- exact tool/capability trajectory;
- verifier pass rate;
- false-completion rate;
- permission-denial integrity;
- context and token efficiency;
- recovery after crash/context replacement;
- stale-state use;
- communication fidelity and echo rejection;
- evidence-chain integrity;
- skill trigger precision;
- human intervention and rollback rates.

Keep raw-model, profile-only, contract, and full-kernel results separate.

## Milestones and proof gates

### M1 — Capability Kernel foundation

Deliverables:

- manifests, registry, broker, authority, evidence, task checkpoints;
- progressive local-model tools;
- package and CLI;
- positive and denied live Qwen proofs.

Gate: complete on branch commit `839022f`.

### M2 — World state and active observations

Deliverables:

- source-attributed TTL observations;
- state snapshots and change feed;
- process/window/mesh/resource adapters;
- stale-state tests;
- model-visible summary with sensitive-value filtering.

Gate: a replacement Qwen answers current machine/mesh state from verified,
fresh observations and refreshes expired facts rather than inventing them.

Progress:

- durable source-attributed observations, TTL freshness, confidence, sensitive
  value hashing, change feed, evidence linkage, broker query capability, CLI
  inspection, and runtime-state seeding are implemented;
- bounded read-only collectors now cover mesh roles, visible windows, process
  inventory, Windows services, NVIDIA GPU resources, and SelfConnect platform
  capabilities without collecting process command lines, executable paths,
  raw window text, environment variables, or credentials;
- missing or stale state triggers one broker-owned authoritative refresh and
  records refresh request/completion evidence;
- live proof passed with a fresh Qwen discovering and reading its own current
  runtime identity/model/authority observation and returning
  `WORLD_STATE_MAGIC_OK`;
- a second live proof started with missing GPU state, refreshed it through
  `nvidia-smi`, observed the RTX 5090, and returned
  `GPU_ACTIVE_REFRESH_OK`;
- event-driven UIA/process/service subscriptions remain future active-perception
  work; M2's deterministic on-demand observation and stale-refresh foundation
  is complete.

### M3 — Task runtime, completion integrity, and recovery

Deliverables:

- evidence-satisfies-predicate completion gates;
- budgets, deadlines, bounded retries, repair policies;
- resume after process termination;
- task ownership and cancellation;
- scheduler/heartbeat integration.

Gate: kill and replace a Qwen process mid-task; the successor re-derives current
authority, resumes from the checkpoint, does not repeat completed mutations,
and cannot mark completion without verifier evidence.

Progress:

- runtime-owned completion predicates now require the matching broker
  `capability_completed` event, execution identity, successful adapter output,
  and successful independent verification; model assertions are not accepted;
- task snapshots are sequence-numbered and hash-linked into an append-only
  checkpoint journal, with stale-writer fork and restored-snapshot rollback
  detection;
- a successor reconciles a witnessed completed execution without invoking its
  adapter again;
- an interrupted running step without completion evidence fails closed as
  ambiguous and is blocked rather than automatically repeated;
- the latest valid journal entry can restore a corrupt or missing current
  snapshot;
- persisted task deadlines and total-attempt budgets are checked before
  admission and block unstarted work without invoking its capability;
- cancellation and retry are bound to the task's immutable owner principal and
  emit broker evidence;
- per-step retry counts and total task attempts are bounded, with exhaustion
  failing closed;
- the live Qwen kill-and-replace proof passed on 2026-07-25: distinct
  predecessor and successor instances returned real model acknowledgements, the
  predecessor process was terminated, the successor re-derived write denial,
  resumed and completed only the remaining verified step, did not repeat the
  completed mutation, and blocked an ambiguous interrupted mutation;
- proof artifact:
  `proofs/capability_os/m3_live_qwen_recovery_20260725.json`.

Gate: complete.

### M4 — MCP capability bridge

Deliverables:

- trusted MCP configuration loader;
- schema-to-manifest conversion;
- capability quarantine on schema drift;
- server-side credentials;
- timeout and evidence handling.

Gate: after a written threat model, Qwen discovers and uses one read-only MCP tool without receiving the
entire server catalog or credentials; a changed schema fails closed.

### M5 — Visual specialist

Deliverables:

- target-window-only capture;
- structured VLM observations;
- UIA/OCR/VLM arbitration;
- prompt-injection boundary;
- VRAM admission control.

Gate: primary Qwen and visual specialist coexist safely on the RTX 5090 or
swap predictably, identify an owned test UI, and complete a verified state
transition without raw coordinate guessing.

### M6 — Shadow skill compiler

Deliverables:

- trace distillation;
- sandbox replay;
- permission inference;
- verifier synthesis constraints;
- review/signing workflow.

Gate: a repeated owned test procedure becomes a candidate, passes replay and
adversarial tests, but remains unusable until separately approved.

### M7 — Capability OS release candidate

Deliverables:

- integrated routing, world state, tasks, perception, MCP, evidence, and skills;
- installer and migration plan;
- governance profiles;
- regression and adversarial benchmark suite.

Gate: defaults remain fail-closed, proven SelfConnect tests pass, repeated local
agent evaluations meet published thresholds, and every claim is reflected in
the claim/evidence matrix.

## Current implementation map

- Capability kernel: `selfconnect_capabilities/`
- Local model harness: `sc_local_agent_runtime.py`,
  `sc_local_agent_harness.py`
- Win32/UIA tools: `sc_cli.py`, `self_connect.py`
- Mesh identity/events: `sc_mesh_registry.py`
- Fabric/control plane: `sc_fabric_*.py`
- Identity/TPM: `sc_identity.py`, `sc_tpm_attestation.py`
- Existing visual server: `vision_server/`
- Capability proofs: `tests/test_capability_kernel.py`,
  `benchmarks/local_agent_model_benchmark.py`

## Decision log

- Keep development in the SelfConnect repository because the kernel depends on
  existing identity, Win32, mesh, evidence, and packaging layers.
- Isolate work through branch `feature/capability-kernel-v1` and a separate Git
  worktree.
- Do not split a new repository until a stable, independently versionable API
  emerges.
- Keep generated skills in shadow mode.
- Prefer one-level progressive disclosure; additional disclosure levels require
  evidence that they improve this workload.
- Treat the capability kernel as strong product engineering. Center novelty
  investigation on SelfConnect's identity-bound OS transport and verified
  dual-plane composition.
- Separate ledger integrity from completion integrity. The broker records every
  attempted action and runtime policy decision. A model's completion claim is
  never evidence; task completion is accepted only when independent evidence
  satisfies the task predicate.
- Do not require ceremonial denied mutation calls. Trusted-host preflight
  evaluates the policy question and emits its decision before model execution.
- Treat exact required-call contracts as legacy trajectory diagnostics, not
  task-success enforcement or the primary model-selection score.
- Select models with hard gates first (zero false completion, zero policy
  violations, working dialect adapter), then rank survivors by outcome,
  latency, and VRAM.
- The repeated constraint-first selection gate is complete. At temperature 0,
  seed 42, and 32K context, Qwen 3.6 27B passed 10/10 known and 10/10 holdout
  runs with full outcomes and evidence. GPT-OSS 20B passed only 4/10 known
  hard-gate runs despite its latency and VRAM advantage. Qwen therefore remains
  the governed local default; GPT-OSS remains an optional lower-risk fast path.
- Benchmark runs use unique partial files, atomic promotion, orphan-worker
  detection, fixed-configuration resume checks, and per-report SHA-256
  fingerprints. A pre-fix overlapping run was invalidated and is not evidence.
- Re-sequence full task recovery and injection hardening before the MCP bridge
  because external schemas, descriptions, and results expand the attack and
  partial-state surface.
- Manifest digests are computed from canonical parsed content, not raw file
  bytes; Git CRLF/LF conversion must not change a manifest identity.
- Nemotron-Terminal-32B remains not evaluated for terminal competence. Its
  historical 10/18 run measured Ollama/OpenAI tool-call dialect incompatibility
  against a model trained for the Terminus 2 structured-command scaffold.
