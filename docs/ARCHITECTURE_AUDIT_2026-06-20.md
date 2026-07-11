# SelfConnect Deep Architecture Audit - June 20, 2026

## Executive Summary

SelfConnect is not at the limit. It is at an architectural inflection point.

> Win32 should remain the discovery, identity, semantic-control, and bootstrap
> layer, but `WM_COPYDATA` should not remain the high-rate agent data plane.

The objective is an operating-system-native agent fabric that minimizes
screenshots, minimizes model calls, minimizes token consumption, and maximizes
deterministic execution.

## Key Themes

### Critical Hot-Path Issues

1. `WM_COPYDATA` head-of-line blocking.
2. OS identity verification gaps.
3. Dynamic atom growth risks.
4. Discovery/listener architecture mismatches.
5. Handshake correlation weaknesses.
6. 64-bit ABI correctness concerns.
7. Governance maturity ahead of execution fabric maturity.

## Governance vs Throughput

Current measurements indicate policy evaluation is not the bottleneck.

Focus areas:

- signing costs;
- verification costs;
- evidence persistence costs;
- HTTP verification overhead;
- synchronous execution paths.

Recommended pattern:

```text
Sign once, MAC many.
```

Establish strong authority once at session start, then use sequence-numbered,
deadline-bound MAC/AEAD frames for high-rate traffic inside that session.

## Fabric V2

### Architecture

```text
External Models / MCP / A2A
  -> Boundary Adapters
  -> SelfConnect Host Service
  -> Authenticated IPC
  -> Session Router
  -> Agent Fabric
```

Core capabilities:

- Named Pipes;
- IOCP;
- bounded mailboxes;
- deadlines;
- backpressure;
- shared evidence;
- shared inference.

## Computer-Use Strategy

Execution hierarchy:

0. deterministic replay;
1. native APIs;
2. Win32/UIA semantic execution;
3. cached scene graph;
4. local OCR and vision;
5. full multimodal reasoning;
6. mouse and keyboard fallback.

Vision should be escalation, not the primary execution path.

## Scaling Beyond The Current Mesh

The benchmark target is not a magic literal count. The goal is to prove that the
mesh works beyond the current 3-to-6-agent working set and continues to behave
under load, slow consumers, migration, and failure.

Agents should not be permanent conversations.

Agent definition:

- identity;
- capabilities;
- mailbox;
- state;
- budget;
- optional model access.

Use:

- shared blackboard;
- shared evidence;
- shared inference scheduler;
- context capsules;
- runbook replay.

Avoid:

- transcript forwarding;
- screenshot forwarding;
- duplicate reasoning.

## Legacy Concepts Worth Reusing

- blackboard systems;
- Linda tuple spaces;
- ring-buffer dispatch;
- event sourcing;
- actor mailboxes.

## Cross-Platform Direction

### Windows

- `WM_COPYDATA` bootstrap;
- Named Pipes + IOCP;
- Win32;
- UIA;
- DXGI;
- ConPTY.

### Linux

- `SOCK_SEQPACKET`;
- `memfd`;
- `eventfd`;
- `pidfd`;
- `epoll` / `io_uring`;
- PTY;
- AT-SPI2.

### macOS

- XPC;
- `AXUIElement`;
- ScreenCaptureKit;
- PTY.

## Priority Roadmap

### P0

- identity fixes;
- discovery fixes;
- handshake fixes;
- atom cleanup;
- ABI correctness;
- N-agent scale benchmark beyond the current live mesh size.

### P1

Fabric V2.

### P2

Token efficiency.

### P3

Fast perception.

### P4

Cross-platform adapters.

## Bottom Line

SelfConnect should become a governed, model-independent,
operating-system-native agent fabric where known work executes deterministically,
semantic operating-system information replaces most computer vision, visual
reasoning is reserved for ambiguity, and many logical agents share a common
inference and evidence substrate.

The goal is not to process screenshots faster.

The goal is to eliminate the need for screenshots whenever possible.
