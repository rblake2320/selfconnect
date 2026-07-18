# Decision Record

## 2026-07-17 - Use Native Advisory Locks for Task Claims

### Decision

Use the operating system's cross-process advisory lock on a persistent file:
`msvcrt.locking` on Windows and `fcntl.flock` on POSIX. Never use pathname
deletion, PID age, or a written owner record as the exclusivity authority.

### Why

Pathname locks require a safe stale-break operation, but portable unlink APIs
cannot condition deletion on inode identity. Age-based breaking can delete a
live owner, PID reuse weakens liveness checks, failed/partial record writes can
strand malformed locks, and compare-then-unlink races can delete a successor.
Native locks already provide the needed lifecycle: descriptor close and
process death release the kernel-held lock without deleting the pathname.

### Consequences

- Lock files persist after release and can be reused safely.
- `stale_after` remains an accepted compatibility parameter but never permits
  breaking a live native lock.
- Process death releases the lock without a stale-record parser.
- Unlock/close failures surface as `LockReleaseError`; they are not reported as
  successful release.
- The mechanism is advisory and local-filesystem scoped, not a distributed or
  hostile-filesystem locking guarantee.
- On POSIX, a process must not fork while holding a task lock unless the child
  closes the inherited descriptor immediately. `flock` follows the inherited
  open-file description, so a child can otherwise retain or release the
  parent's lock. SelfConnect's supported Windows and spawn-based process paths
  do not use this fork pattern.
- The lock directory must be stable and excluded from temporary-file reapers or
  manual pathname replacement. POSIX `flock` protects the opened inode, not the
  pathname; replacing the persistent lock file could create a second lock
  authority. SelfConnect never unlinks these files.

## 2026-07-15 - Test Discovery Against an Exact External Window

### Decision

Keep `find_target` self-exclusion unchanged. For the live SDK smoke, enumerate
visible windows, remove the resolved caller PID, select a title that matches
exactly one remaining window, and require discovery to return that window's
HWND and PID. Skip explicitly when the session exposes no safe unique target.

### Why

The failed CI assertion tested an impossible condition: it selected the
caller's own first window and then expected a self-excluding API to return it.
A broad first-word query could also resolve a different window and still pass.
The exact external-target assertion now checks the intended safety behavior and
the positive discovery behavior together.

### Consequences

- Caller exclusion remains a production invariant.
- Duplicate titles cannot create a false-positive smoke result.
- Sessions without a distinguishable external window report a bounded skip
  instead of weakening discovery safety or manufacturing a target.

## 2026-07-15 - Bind Public Claims With Stable Tagged Excerpts

### Decision

Treat a public README statement as mechanically governed only when it is inside
an exact `SC-CLAIM:<id> START/END` block, the ID maps back to the same ledger
entry, and the normalized excerpt hash matches `release/claims.json`.

### Why

The previous release percentage answered only "how many release ledger rows
are internally valid?" It did not answer "how many README capability claims
are registered?" Natural-language claim classification is not reliable enough
to silently expand that denominator. Exact tags make the controlled set
deterministic while an explicit `PARTIAL` boundary prevents a complete-coverage
claim about untagged prose.

Historical screenshots and logs are useful reduction-to-practice records, but
they do not support universal behavior, remote model identity, patentability,
authorization, or production conclusions. Their public wording therefore says
`implemented and exercised` and names the represented boundary.

### Consequences

- A capability edit and its claim record are one atomic change.
- Exact excerpt hashes make statement-to-ledger mismatches fail the release
  audit, even when the tag ID still exists.
- Human review remains required for untagged natural-language prose.
- This control does not provide a legal, compliance, novelty, or authorization
  determination.

## 2026-07-15 - Select Terminal Input by Verified Window Class

### Decision

Use `WriteConsoleInputW` for `ConsoleWindowClass`, exact-HWND `WM_CHAR` for the
tested Windows Terminal CASCADIA surface, and make all paths return structured
acceptance evidence. Never convert a console-write failure into apparent
success by falling back to `PostMessageW`.

### Why

The Windows API contracts and the live defect are aligned: `PostMessageW`
returns before receiver processing. Its success proves queueing only. The
console input buffer is the native input boundary for `ConsoleWindowClass`.

Cross-console access temporarily changes caller console attachment. Success is
therefore conditional on both a complete target write and explicit restoration
to a snapshotted original console PID. Blind restoration to a parent process is
not sufficient evidence.

### Consequences

- Callers can distinguish `win32_console_input`,
  `postmessage_wm_char`, and `sendinput`.
- `delivery_verified` remains false for raw transport acceptance.
- Independent readback, ACK, or receiver effect is needed for delivery claims.
- Incompatible explicit transport requests fail closed.

## 2026-07-17 - Pin the Scale Consumer Before Enabling the Producer

### Decision

The restricted producer accepts only the immutable ecosystem main commit
`f07b4a72ca900da531a8596168600977a6678427` as its consumer contract. Source
identity uses strict UTF-8 and newline-canonical bytes so Windows checkout line
endings do not change the evidence identity.

### Why

The consumer must exist on protected main before a producer can cite it. A
feature-branch SHA can disappear or change review state, while a placeholder
only proves the workflow cannot run. Canonical source bytes prevent the same
source from producing different identities on LF and CRLF checkouts.

### Consequences

- Code, workflow environment, fixture manifest, and tests share one consumer
  pin.
- Changing the producer source regenerates the fixture identity by design.
- The fixture proves deterministic contract compatibility only. Live readiness
  still requires a protected runner, provider credentials, GitHub attestation,
  successful 10/15/20 execution, and consumer acceptance.

## 2026-07-18 - Publish Scale Evidence Only After Cleanup

### Decision

Do not make a portable rung visible as `PASS` until all captured process roots
and still-live terminal launchers have completed bounded termination. Emit a
strict cleanup receipt in a separately attested control archive and bind it to
the exact portable rung digest.

### Why

Writing the rung inside the protected work block allowed a partial `PASS` file
to exist before the `finally` cleanup ran. A later cleanup failure prevented the
manifest, but the standalone rung still represented an ordering claim that had
not yet become true. Adding cleanup fields to the rung would break the pinned
consumer's exact schema, so operational cleanup controls remain separate.

### Consequences

- Cleanup failure suppresses the rung and bundle manifest.
- Atomic writes prevent a torn rung or control receipt from being published.
- The existing portable consumer bytes and exact-key schema do not change.
- Cleanup controls establish process teardown for the producer-owned roots;
  they do not attest runner ephemerality or complete a live scale run.
