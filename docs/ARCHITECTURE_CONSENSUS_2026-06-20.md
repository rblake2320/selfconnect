# SelfConnect Multi-Model Architecture Consensus - June 20, 2026

Private architecture planning note. Not approved for public disclosure.

## Merged Position

```text
File now on what is already proven.
Benchmark now against the current transport.
Then build Fabric V2 and prove the delta.
```

This avoids the classic engineering mistake: refactor first, measure later, and
then have no baseline proving the refactor mattered.

## Consensus Findings

### 1. Treat The Audit As The Architecture Pivot

The current architecture should keep Win32 where it is strongest:

- discovery;
- window identity;
- semantic control;
- UIA execution;
- local trust anchoring;
- bootstrap paths.

The high-rate data plane should move away from `WM_COPYDATA` toward a bounded
local fabric: Named Pipes, IOCP, deadlines, backpressure, and mailboxes.

### 2. The Next Proof Is Scale, Not Another Demo

The next important proof is not another single-agent capability demo. It is a
governed scale benchmark that shows SelfConnect can keep working beyond the
current live mesh size.

Use 20 agents as a stress profile, not a fixed product claim. The real target is
stable N-agent operation with:

- routing correctness;
- bounded queue growth;
- slow-consumer handling;
- replay rejection;
- audit persistence;
- migration continuity;
- no wrong-window writes;
- no echo/self-response confusion.

### 3. Sign Once, MAC Many

Governed mode should establish strong authority once, then use a cheaper
per-message authenticator inside the session.

Recommended pattern:

1. establish session authority with Ed25519, TPM/CNG, or attested identity where
   available;
2. derive a session key;
3. authenticate high-rate traffic with sequence-numbered HMAC/AEAD frames;
4. reject replayed, stale, out-of-window, or wrong-lease frames;
5. anchor evidence into the hash-chained event log and enterprise WORM path
   where required.

This keeps governance overhead low without weakening the authority story.

### 4. Formalize The Execution Hierarchy

Execution order should become a core primitive:

0. deterministic replay;
1. native APIs;
2. Win32/UIA semantic execution;
3. cached scene graph;
4. local OCR and vision;
5. full multimodal reasoning;
6. mouse and keyboard fallback.

This turns SelfConnect from "models operating computers" into "agents compiling
successful work into deterministic, auditable, low-token execution paths."

Known workflows should run with zero or near-zero model calls. Vision and full
multimodal reasoning should be escalation paths, not the default.

### 5. Governance Ahead Of Fabric Is A Wedge, Not A Weakness

Governance being ahead of execution is not a weakness. It is the regulated-market
wedge.

The execution fabric now needs to catch up.

That means the right posture is:

- do not apologize for the governance layer;
- do not slow normal/explore mode with government controls;
- keep safety invariants always on: target guard, birth IDs, echo-filtered
  readback, and mesh history;
- build Fabric V2 as an adapter path that can be benchmarked against today's
  transport.

## Patent Claim Families To Track

The strongest already-proven families remain:

- OS-native channel routing by target surface;
- fail-closed target verification;
- echo-filtered readback;
- SID-bound role leases;
- local-model action selection through governed native transport;
- tamper-evident mesh history.

The new families to explicitly track:

- cost-ordered execution hierarchy;
- deterministic replay before model reasoning;
- sign-once/MAC-many governed session fabric;
- shared evidence and blackboard substrate for many logical agents.

## Sequencing Rule

Do not block filing on Fabric V2.

File based on the already-proven composition, then use the benchmark and Fabric
V2 work as continuation/dependent-claim material.
