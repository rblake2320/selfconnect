# Local Model Smart Wrapper

Last updated: 2026-06-18

## Core Point

A small local model should not be trusted as a free-form agent. Treat it as a
planner inside a stronger wrapper:

```text
small local model
  -> strict JSON tool plan
  -> schema validation
  -> sandboxed tool execution
  -> test/verifier feedback
  -> durable outbox/inbox
  -> optional visual SelfConnect status
```

This is how a weaker model becomes useful: the model suggests actions, but the
wrapper decides whether those actions are valid, safe, and successful.

## What The Visible Repair Demo Proved

The visible repair demo used `gemma3:latest` through Ollama. The local model:

1. Saw a tiny failing Python task.
2. Returned a constrained JSON plan.
3. Selected `replace_text` with the exact expected old/new text.
4. Selected `notify_codex` with the nonce.
5. The wrapper applied the edit only in a temp sandbox.
6. The wrapper reran the test and required PASS.
7. The wrapper sent a visual status line to `codex-1`.

Important correction: the visual status line may land in Codex's input queue if
Codex is already working. That proves Win32 delivery to the terminal input
surface, not that Codex consumed and acknowledged the message. The durable
handoff should be an outbox record or named-pipe message, with a separate ACK.

## Required Wrapper Pieces

Keep these on for weak local models:

- `temperature=0.0` for tool plans.
- Small tool schemas with one obvious action per step.
- JSON extraction plus strict validation.
- Exact file/path allowlists.
- Exact old/new text checks before editing.
- Temp sandbox first, never repo writes by default.
- Test-before and test-after.
- Retry loop only when the failure is machine-checkable.
- Durable outbox/inbox for agent messages.
- Visual SelfConnect sends only as a human-observable status surface.

## The Message Queue Fix

Do not rely on typing into an active Codex/Claude prompt as the primary control
plane. Use this split:

```text
control truth: local_model_outbox.jsonl or named pipe
human-visible proof: SelfConnect terminal send
ack truth: receiver writes ack record back to inbox/outbox
```

That prevents the exact issue observed in the visible repair demo: the message
appeared in Codex's input area, but Codex was busy, so it did not become a clean
back-and-forth exchange.

## MCP Use

A local model can use MCP-style tools if a local bridge exposes them as
validated tools. The claim depends on the tool:

- local file/search/test tools: can remain airgapped;
- browser/web/research tools: not airgapped if they use the internet;
- SelfConnect MCP send tools: must stay guarded by target verification and
  explicit allow flags.

For local models, prefer a small explicit tool bridge first:

```text
read_file
search_repo
replace_text
run_tests
write_outbox
send_visible_status
```

Add web research only as a separate profile so the artifact can say clearly
whether the run was local-only or networked.

## Next Build

The next real upgrade is a long-running local role:

```text
LOCAL-OLLAMA-1
  heartbeat -> mesh registry
  inbox poll -> local JSON tool plan
  tool execution -> verifier
  outbox write -> ACK required
  optional SelfConnect visible status
```

That gives us a local worker that can participate in the mesh without needing to
be a high-end model.
