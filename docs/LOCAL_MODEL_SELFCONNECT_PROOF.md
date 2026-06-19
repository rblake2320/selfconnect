# Local Model SelfConnect Proof

Last updated: 2026-06-18

## Result

PASS: a local Ollama model generated a packet, and SelfConnect delivered that
packet through the Win32 terminal transport into a throwaway receiver process.

Artifact:

```text
experiments/win32_probe/results/local_model_selfconnect_pass_BB728109.json
```

Probe:

```powershell
python experiments\win32_probe\local_model_selfconnect_probe.py --model hermes3:3b
```

## Action-Agent Result

PASS: a local Ollama model selected a structured tool action, the harness
validated the action, SelfConnect executed it through the guarded Win32 send
path, and the throwaway receiver log verified the nonce.

Artifact:

```text
experiments/win32_probe/results/local_model_action_agent_pass_791D23F5.json
```

Probe:

```powershell
python experiments\win32_probe\local_model_action_agent_probe.py --model gemma3:latest
```

Control path:

```text
Ollama local JSON action
  -> action validator
  -> SelfConnect Win32 send_text_to_window
  -> throwaway receiver log verification
```

This is the stronger local proof: the model did not only generate text, it chose
an allowed action that was executed locally after validation.

## Live Codex Target Escalation

PASS: the action-agent flow was escalated from a throwaway receiver to the live
registered `codex-1` terminal. The target was verified before send:

- HWND: `28443124`
- PID: `16680`
- exe: `WindowsTerminal.exe`
- class: `CASCADIA_HOSTING_WINDOW_CLASS`
- title: `codex 1`

The local model selected a `selfconnect_send` JSON action, the action validator
accepted it, SelfConnect sent one compact non-command status line to `codex-1`,
and UIA readback found the nonce in the Codex terminal text.

Artifact:

```text
experiments/win32_probe/results/local_model_to_codex1_pass_43832F35.json
```

Control path:

```text
Ollama localhost
  -> JSON action
  -> validated selfconnect_send
  -> guarded Win32 send to registered codex-1 HWND
  -> UIA readback contains nonce
```

This proves a local model can send into a live mesh participant when the target
guard passes. It is not a command-execution proof and should stay constrained to
short coordination/status packets until a lease-gated local-model role is wired.

## Durable Local Model Role

PASS: `LOCAL-OLLAMA-1` now has a durable mailbox role that does not depend on a
visible terminal staying open. The role is registered as a virtual mesh
participant with `hwnd=0`, `class_name=virtual`, `transport=mailbox`, stable
`birth_id`, generation, model, inbox, and outbox paths.

CLI:

```powershell
selfconnect-local-model init --role local-ollama-1 --model gemma3:latest
selfconnect-local-model inbox --role local-ollama-1 --from-role codex-1 --text "task text"
selfconnect-local-model outbox --role local-ollama-1 --to-role codex-1 --text "ACK ..."
selfconnect-local-model read --role local-ollama-1 --box inbox
selfconnect-local-model status --role local-ollama-1
```

Module:

```text
sc_local_model_role.py
```

Tests:

```text
tests/test_local_model_role.py
```

This closes the one-shot demo gap: when a visible local-model terminal closes,
the role remains addressable through its mailbox and mesh registry row. The
mailbox is a coordination channel, not an authority grant. Actual actions still
flow through the validator and guarded transport.

## Visible Local Demo

PASS: the same action-agent flow was rerun with visible throwaway terminals left
open for human inspection.

Visible windows from the run:

```text
LOCAL-OLLAMA-1_VISIBLE_2DD47334
SC_VISIBLE_RECEIVER_2DD47334
```

Artifact:

```text
experiments/win32_probe/results/local_model_visible_action_2DD47334.json
```

Probe:

```powershell
python experiments\win32_probe\local_model_visible_action_demo.py --model gemma3:latest
```

The actor terminal prints the raw local model JSON, the validated tool action,
the guarded SelfConnect send result, and a PASS line. The receiver terminal
prints the delivered packet.

## Visible Repair + Codex Status Demo

PASS: the local model was given a tiny broken Python task in a temporary sandbox,
selected a constrained repair action, the harness applied the sandbox-only edit,
and the local unittest suite moved from fail to pass.

The demo also sent a visual status line to `codex-1` through guarded
SelfConnect transport. In an active Codex session this can land in the input
queue rather than being consumed immediately, so it is evidence of delivery to
the Codex terminal surface, not a completed Codex ACK.

Visible actor window:

```text
LOCAL-OLLAMA-1_REPAIR_CAF5E8AB
```

Artifact:

```text
experiments/win32_probe/results/local_model_visible_repair_CAF5E8AB.json
```

Probe:

```powershell
python experiments\win32_probe\local_model_visible_repair_demo.py --model gemma3:latest --codex-role codex-1
```

The local model returned this repair plan shape:

```json
{
  "steps": [
    {
      "tool": "replace_text",
      "args": {
        "file": "buggy_math.py",
        "old": "return a - b",
        "new": "return a + b"
      }
    },
    {
      "tool": "notify_codex",
      "args": {
        "message": "Fixed the addition logic in buggy_math.py using a + b. NONCE=..."
      }
    }
  ]
}
```

The edit was constrained to a temp sandbox, not repo source. The status packet to
Codex was a one-line message, not a command. Durable local-model coordination
should use an outbox/inbox or named-pipe control plane, with visual sends used
only as human-observable status.

Wrapper design:

```text
docs/LOCAL_MODEL_SMART_WRAPPER.md
```

## Mixed Claude Responder Result

PARTIAL/BLOCKED: a local Ollama model produced a valid two-step plan to spawn a
Claude responder and ask for an ACK. The harness launched a throwaway Claude
Code responder workspace, delivered the request through SelfConnect, and reached
Claude's command execution boundary. Claude's auto-mode classifier denied the
`sc_cli.py send` command, so no ACK was written to the receiver.

Artifact:

```text
experiments/win32_probe/results/local_model_spawn_claude_fail_310BEC4E.json
```

Probe:

```powershell
python experiments\win32_probe\local_model_spawn_claude_probe.py --model gemma3:latest
```

This is the correct safety result for an untrusted peer-to-Claude command. It
proves the local model can plan and initiate the mixed exchange, but it also
proves that Claude Code should not execute peer-issued window-injection commands
without an explicit trusted responder profile, approved permission rule, or
authenticated lease/approval channel.

The full mixed path is not an airgap claim because Claude Code is cloud-connected.
Use this result only as a mesh interoperability boundary test.

## Hardware Safety

The tested machine had an RTX 5090 with about 32 GB VRAM. The selected model was
`hermes3:3b`, already installed through Ollama and small enough for a safe smoke
test. During the successful run, VRAM stayed around 2.7 GB used with about
29.4 GB free.

Do not use `llama4:scout` for this smoke path; it is far larger and unnecessary
for validating SelfConnect transport.

## Control Path

```text
Ollama local generate
  -> SelfConnect Win32 send_text_to_window
  -> throwaway terminal stdin
  -> receiver log verification
```

No cloud model, MCP, CDP, browser extension, WebDriver, or API-key control path
was required for this local-model transport proof. Ollama's local HTTP endpoint
was used only to run the local model on the machine.

## Honest Scope

This proves a local model can participate in the SelfConnect process through a
local wrapper/harness and can select an allowed action that is executed after
validation. It does not yet prove a fully autonomous long-running local mesh
role with heartbeat, registry lease, compact handoff, or multi-turn planner
state. Those are the next layers to build.
