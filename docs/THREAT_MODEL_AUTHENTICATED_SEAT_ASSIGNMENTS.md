# Threat Model: Authenticated Seat Assignments

Status: release-gating draft
Date: 2026-08-12

## Security objective

A coordinator may cause exactly one enrolled seat to process a bounded
assignment and may accept progress/completion only when the assigned seat signs
the canonical receipt chain. A blocked or rejected seat may be replaced only
through an authenticated, recoverable continuity ceremony. Transport success,
screen text and local database rows are not substitutes for authorship.

## Adversary in scope

The adversary may run an unprivileged process as the same interactive Windows
user on the same desktop. It may:

- enumerate, reuse and race HWNDs; observe shared WindowsTerminal.exe PID,
  titles, classes and process start times;
- call `PostMessage(WM_CHAR)`, focus windows, write forged text into composers,
  and make text appear in terminal scrollback;
- read UI Automation/OCR output and public filesystem data available to that
  user;
- call public Python/Node APIs, mutate caller-owned dictionaries, replay
  captured signed records and race callbacks;
- write unauthenticated registry rows or append malformed/unhashed files where
  ACLs permit;
- crash or restart coordinator, receiver, worker or terminal between durable
  state-machine boundaries;
- compromise one seat private key or one response-channel receiver key, after
  which revocation and blast-radius controls must still work.

The adversary does not initially possess the independently protected private
keys of the coordinator, enrollment authority, assigned seat and response
receiver. Compromise of any one key is considered and must not silently become
compromise of every role.

## Out of scope for the current local boundary

- Windows kernel, administrator/SYSTEM or cryptographic-library compromise;
- malicious firmware, TPM hardware extraction and physical memory attacks;
- denial of service by an administrator who can terminate every process or
  delete every protected store;
- confidentiality of terminal pixels from the interactive user.

These exclusions do not permit claims that DPAPI is TPM-backed or that local
same-user files are independently administered.

## Evidence classes

| Evidence | May establish | Must never establish alone |
|---|---|---|
| HWND/PID/title/class/process start/UIA runtime ID | routing and live drift observation | durable seat identity or authorization |
| `PostMessage`/input queue acceptance | OS queue accepted characters | delivery to a model, parsing, processing or completion |
| UIA/OCR/screenshot/composer clear | diagnostics and operator recovery | authorship, approval, refusal, processing or completion |
| registry row/event hash chain | tamper-evident operational observation | who asserted the row or whether work ran |
| coordinator-signed assignment | exact authorized payload and intended enrolled seat/bindings | receipt that the seat processed it |
| OS-attested private channel + independently signed channel evidence | response came through the expected protected channel | seat processing unless joined to seat signature |
| seat-signed receipt chain | accepted/working/blocked/completed state for the assignment | coordinator durable consumption until ACK |
| coordinator post-commit ACK | receipt was durably consumed | external publication or human approval |

## Trust roles and separation

- Enrollment authority signs seat enrollment and operation challenges.
- Coordinator signs assignments and receipt ACKs.
- Seat signs state receipts and challenge proofs.
- Response receiver signs OS-derived private-channel evidence.
- Revocation authority signs monotonic fresh key-revocation snapshots.

High assurance requires distinct keys for these roles where stated by policy.
Trust updates, authority rotation and recovery require signed monotonic state;
an untrusted caller may not self-enroll a replacement root.

## Mandatory invariants

1. Raw composer text is never actionable. The receiver exposes a payload only
   after canonical parsing, signature verification and durable consumption.
2. Exact target/tab/channel identity is checked before and after every relevant
   side-effect boundary.
3. Signed records are canonical, bounded, fresh and replay/fork resistant.
4. Revocation is resolved from a fresh signed snapshot, not an empty default.
5. State machines survive restart at every durable boundary; exact retries
   resume or return an idempotent result, while conflicts fail closed.
6. No screen, queue, registry or audit observation may upgrade authority.
7. A final success includes seat completion plus coordinator durable ACK.
8. Failover never auto-kills a process; it quarantines the exact old identity
   and binds successor lineage to the authorizing receipt.

## Required live ceremony

On a real Windows desktop with at least two seats sharing WindowsTerminal.exe:

1. issue to exact seat A; accept and work through authenticated ingress;
2. reject unsigned/wrong-key composer text and wrong seat/tab/HWND/channel;
3. emit signed blocked receipt, quarantine exact A, enroll distinct fresh B,
   resume through signed lineage, complete and ACK;
4. replay old assignment/receipt/channel evidence and revoke a key mid-flight;
5. prove UIA/OCR/composer-clear and forged registry/event records remain
   diagnostics only;
6. restart each participant at recorded saga boundaries and recover exactly.

No module-only test count replaces this ceremony.
