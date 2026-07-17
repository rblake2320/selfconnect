# Decision Record

## 2026-07-17 - Make File-Lock Release Verifiable and Recoverable

### Decision

Give every `FileLock` acquisition a unique owner record, track records owned by
this process, verify the record before deletion, and retry transient Windows
sharing violations to a monotonic deadline. If deletion remains impossible,
raise `LockReleaseError` and mark the owner record inactive so a later claimant
can recover the orphan once filesystem access returns.

### Why

Closing a file descriptor does not remove its pathname. On Windows, another
reader can briefly prevent deletion. Treating that failure as success leaves a
lock file whose PID is still alive even though its owning critical section has
ended. PID liveness alone therefore cannot distinguish an active same-process
lock from an abandoned one, and callers that retry `LockTimeout` can loop
forever.

Blind unlink retries are also unsafe: if a lock pathname were replaced between
attempts, an old holder could delete a successor's lock. Comparing the unique
owner record before every delete keeps recovery scoped to the acquisition that
is actually being released.

### Consequences

- Transient Windows sharing violations no longer strand the board lock.
- Persistent release failure is explicit and bounded rather than reported as
  success.
- A later same-process claimant can identify an abandoned token without waiting
  for the five-minute age threshold.
- Legacy two-field lock records remain readable and retain the prior PID/age
  stale policy; only new tokenized records receive immediate same-process
  abandonment detection.
- A filesystem that continuously blocks lock deletion remains unavailable and
  produces a bounded error; this mechanism is not a distributed lock or a
  hostile-filesystem availability guarantee.

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
