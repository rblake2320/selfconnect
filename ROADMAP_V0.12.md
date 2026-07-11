# SelfConnect v0.12 Release Roadmap

Status: release hardening
Branch: `release/v0.12-hardening`
Source baseline: `972ca0b`

## Release Rule

SelfConnect v0.12 is releasable only when the release audit passes from a clean
Git commit and the candidate is compared with a versioned baseline. Faster is
not better when correctness, evidence, target safety, or the product boundary
regresses.

The release audit is the authority for mechanical gates. The claim ledger is
the authority for what may be said publicly. Patent counsel remains the
authority for legal claim language.

## Core Purpose

SelfConnect remains an OS-native Windows AI peer mesh. Improvements must
preserve all of these invariants:

1. The terminal actuation path works without MCP, HTTP, a cloud API, or a
   broker.
2. Target facts are verified before input in every profile.
3. Terminal, browser, and metadata traffic remain separate channel species.
4. Readback distinguishes local echo from external output.
5. MCP, Fabric, NATS, TPM, service mode, and enterprise controls remain
   optional adapters around the core path.
6. Normal mode is not slowed by governed or experimental dependencies.
7. Experiments do not become release claims without reproducible evidence.
8. A narrower truthful claim is preferred to a broader unsupported claim.

## Version-Control Discipline

- Keep release work on `release/v0.12-hardening` until reviewed.
- Do not tag from a dirty worktree.
- Do not fold unrelated user changes into release commits.
- Use one concern per commit: truth/docs, packaging, measurement, transport,
  security, orchestration, then QA.
- Every benchmark report records branch, commit, dirty state, environment, and
  artifact hashes.
- Never rewrite or delete failed evidence. Mark it failed, superseded, or
  invalidated and retain the reason.
- Create annotated tag `v0.12.0` only after source and runtime audits pass from
  the exact candidate commit.

## Measurement Protocol

Capture a JSON audit before and after each phase:

```powershell
python tools/release_gate.py audit --root . --build-wheel --run-tests --run-ruff `
  --benchmark experiments/fabric_v2/results/SC_FABRIC_SERVICE_20260621_1135_redacted.json `
  --output release/results/candidate.json

python tools/release_gate.py compare `
  release/results/baseline.json release/results/candidate.json
```

The comparison reports objective deltas only. It does not manufacture a single
quality score. Required dimensions are:

- release blockers and warnings;
- public claims with valid evidence;
- test, lint, build, and wheel-content results;
- transport/governance p50, p95, and p99;
- audit and end-to-end p99;
- dropped messages, wrong-target writes, replay acceptance, and model calls;
- resource floors and live-agent ACK accuracy when a live ladder is run.

Comparisons are valid only when transport, agent count, profile, workload, and
environment class match. Otherwise the tool reports the runs as
non-comparable.

The default Ruff gate covers the shipped wheel modules, ClaudeGo package, and
release-gate code. It must be reported as package-scoped; it is not permission
to describe unrelated legacy/experiment files as repository-wide Ruff clean.

## Phases

| Phase | Scope | Exit gate |
| --- | --- | --- |
| P0 Truth | Clean Git state, version/license consistency, structured claims | No identity or claim-evidence failures |
| P1 Package | Ship every supported v0.12 module and declared optional dependency | Built wheel imports and package audit pass |
| P2 Transport | Fabric service, short visible doorbells, event-driven readback | Same workload is faster with zero correctness regression |
| P3 Security | Guard all send paths, governed identity, attestation, off-host anchor | Wrong target/replay/stale identity remain fail-closed |
| P4 Orchestration | High-level MCP, canonical spawn, worktrees, TTL, event discipline | Task lifecycle and recovery tests pass |
| P5 QA | Browser routing/parity and refreshed live ladders | Evidence ledger updated with scoped artifacts |

## Claim Discipline

Each release claim in `release/claims.json` must include:

- exact statement and status;
- whether it is approved for release positioning;
- scope and explicit boundary/non-claim;
- verification date;
- immutable evidence path and SHA-256 where applicable (text evidence uses
  LF-normalized bytes so Git line-ending conversion does not create false
  tamper failures);
- machine-readable assertions against structured artifacts.

`pending`, `experiment`, `local-only evidence`, and `not claimed` are valid
statuses. They are not failures unless release text presents them as proven.

Do not use “no prior art,” “patentable method,” “categorically unique,” or
equivalent legal conclusions in release positioning without a documented
search and counsel approval. “No API” statements must name the measured leg;
they cannot imply that hosted model providers made no network calls.

## Procurement-Grade Attestation Gate

TPM support becomes a procurement-grade claim only after a repeatable verifier
artifact proves all of the following on the release candidate:

- hardware-backed key provenance rather than provider availability alone;
- nonce freshness and replay rejection;
- attestation/endorsement chain validation and documented trust anchors;
- declared PCR selection and policy interpretation;
- verifier success and negative tests for altered claims, nonce, key, and PCRs;
- device/OS/firmware scope, failure modes, and fallback behavior;
- redacted evidence tied to commit, machine class, and artifact hashes.

Until then, the approved wording is “TPM capability or TPM-backed signing
probe,” not “platform attestation” or “hardware root of trust established.”

## Approval Evidence Gate

The Telegram approval path becomes a governed operational claim only when one
end-to-end test proves policy decision, escalation, authenticated operator
response, target-guarded injection, replay resistance, timeout/deny behavior,
and audit linkage to the exact action hash. Telegram must be described as an
external cloud dependency. Patentability or uniqueness remains a counsel
question, not a test result.

## Current Blockers

- The integration checkout contains unrelated modified and untracked work that
  must be reviewed and committed selectively; it must not be mass-committed.
- Installed editable metadata reports `0.10.4` while source reports `0.12.0`.
- High-level v0.12 orchestration modules need wheel/import verification.
- Fabric V2 is proven as an optional service path but is not yet the default
  runtime, and no release claim should say otherwise.
- TPM-backed signing is not TPM platform attestation.
- Real-agent scale evidence needs a tracked, redacted artifact before it is a
  portable public release claim.

## Tag Gate

Before tagging:

1. Run the source audit from a clean candidate commit.
2. Build the wheel and test it in a fresh virtual environment.
3. Install that exact wheel and run the runtime audit.
4. Run focused Win32 live probes on the target Windows build.
5. Compare the candidate with the pinned baseline under the same workload.
6. Verify the claim ledger and mesh/event chains.
7. Review public documentation against `release/claims.json`.
8. Record the final audit JSON and wheel SHA-256 beside the release notes.
