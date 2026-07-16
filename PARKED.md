# Parked Work

## P-001 - General Receiver Delivery ACK

- Status: parked, not represented as complete.
- Current replacement: transport-specific acceptance records plus existing
  readback/ACK helpers; the live console proof requires a receiver effect.
- Why parked: a universal ACK must define receiver semantics across terminal
  TUIs and cannot be inferred from one Windows API return.
- Restore condition: a versioned receiver ACK contract with replay handling,
  exact correlation, timeout behavior, and adversarial tests.

## P-002 - Console Input for Windows Terminal Tabs

- Status: parked.
- Current replacement: exact-HWND `WM_CHAR` for the tested CASCADIA surface.
- Why parked: Windows Terminal tabs can share a process; a PID-selected console
  input buffer is not sufficient proof of the intended tab.
- Restore condition: deterministic tab-to-console identity binding plus live
  wrong-tab and stale-tab denial tests.

## P-003 - Console Screen-Buffer Read Parity

- Status: parked.
- Current replacement: UIA readback where supported and workload-specific
  receiver effects/ACKs.
- Why parked: `ReadConsoleOutputW` was unavailable against the recorded
  throwaway host even though console input and target execution succeeded.
- Restore condition: reproduce across supported console hosts, expose
  structured read errors, and prove caller console restoration on every exit.

## P-004 - Caller Without a Restorable Original Console Peer

- Status: intentional fail-closed boundary.
- Current behavior: a cross-console operation is denied before detach when the
  caller is the only known process in its original console.
- Restore condition: a reviewed helper-process design that preserves the
  original console without weakening target identity or error reporting.
