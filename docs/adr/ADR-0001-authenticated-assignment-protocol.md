# ADR-0001: Authenticated Assignment and Receipt Protocol

Status: accepted for implementation; release remains HOLD pending live proof
Date: 2026-08-12

## Context

SelfConnect historically transported coordination as terminal text. A hostile
same-user process can inject indistinguishable text, HWNDs are reusable, Windows
Terminal seats share a process, and UIA/OCR/composer state cannot identify an
author. During the 2026-08-12 session, accepted sends reached dead shells,
messages vanished, duplicate titles existed and an injected key sequence
crashed PowerShell. The transport therefore cannot be the trust boundary.

## Decision

Use a signed, canonical, durable protocol with five phases:

1. Enrollment authority enrolls a seat birth/generation/epoch/full public key.
2. Coordinator issues one inline-payload assignment bound to exact target,
   TerminalTab and response-channel identities.
3. A seat ingress broker parses and durably verifies/consumes the assignment;
   unsigned composer text is discarded as observation.
4. The seat emits a monotonic hash-linked signed receipt chain. The coordinator
   independently verifies and durably commits each receipt.
5. The coordinator returns a signed post-commit ACK. Completion without ACK is
   recoverable, not silently final.

Blocked/rejected recovery uses a durable saga. It records an intent, performs
an exact old-birth compare-and-swap, records the applied quarantine, issues a
successor assignment carrying predecessor and authorizing-receipt hashes, and
requires a signed delivery/admission result. Exact retry resumes the saga;
conflicting replay fails.

## Canonicalization

- JSON objects use unique member names, bounded depth/size/integers, finite
  numbers only and deterministic UTF-8 serialization.
- Unicode normalization policy and cross-language Python/Node vectors are
  required before cross-language claims.
- Caller-owned mappings are snapshotted once before verification and use.

## Revocation and authority lifecycle

Verification resolves fresh, signed, monotonic revocation state for coordinator,
seat and receiver keys. Authority roots use explicit signed rotation/recovery;
existing trust stores cannot be overwritten by a self-asserted key. High
assurance may require quorum approval.

## Consequences

Benefits:

- authorization no longer erases authorship;
- exact payload/seat/resource provenance survives restart;
- UIA/OCR and registry data remain useful without becoming authority;
- replay, fork, wrong-seat and partial-failure behavior is testable.

Costs:

- more keys, stores and state transitions;
- old callers fail until migrated;
- live Windows private-channel and recovery testing is mandatory;
- local mailbox transport alone provides durability, not confidentiality or
  OS channel attestation.

## Rejected alternatives

- Trust terminal text or composer-clear: forgeable and observational.
- Use PID/HWND/title as identity: shared/reusable and not cryptographic.
- Treat hash-chained registry events as processing proof: integrity does not
  prove authorship.
- One shared key for every role: one compromise forges the whole chain.
- Consume a failure receipt before a nonrecoverable sequence: strands failover
  after any crash.

## Verification gates

The release matrix is `Team/tasks/20260812-seven-voice-closure.md` in the parent
workspace. Reviews cite commit SHAs, not moving-tree timestamps. The threat
model is `docs/THREAT_MODEL_AUTHENTICATED_SEAT_ASSIGNMENTS.md`.
