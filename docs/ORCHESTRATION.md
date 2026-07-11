# SelfConnect Orchestration Layer (v0.12.0)

Turns SelfConnect from "inject and hope" into an evented, acknowledged,
governed request/response system — closing every gap found in the July 2026
market review against Claude Code Agent Teams, Gas Town, A2A, and NATS.

## The problem it fixes

The original mesh was fire-and-forget: inject text, wait a heuristic `1s`,
send `\r`, wait `3–5s`, poll the screen at `30s`. Every documented failure
mode (bounce, forget-to-submit, stuck-on-permission) traced to one root
cause — **no acknowledgment**. Reading responses back by scraping the
terminal was the fragile half of the round-trip, and any local process could
forge an injected message.

## The seven modules

| Module | Fixes | Borrowed from |
|--------|-------|---------------|
| `sc_tasks.py` | Fire-and-forget → durable task lifecycle; blind poll → state wait; race-y claiming → file-locked claim; silent hang → retry+dead-letter | A2A task states + Agent Teams file-locked task list |
| `sc_transcript.py` | Screen-scraping → lossless structured transcript tailing | Claude Code session JSONL |
| `sc_hooks.py` + `sc_hook_emit.py` | Timing waits → deterministic hook signals (ack / notification / stop) | Agent Teams `TeammateIdle`/hooks, Gas Town settings-hook mail |
| `sc_done.py` | Completion-by-inference → explicit signed completion verb | Gas Town `gt done` |
| `sc_envelope.py` | Unauthenticated messages → HMAC-signed envelopes + signed agent cards | A2A signed Agent Cards |
| `sc_spawn.py` | Ad-hoc spawn → budget-gated, worktree-isolated, ready-checked, acked spawn | agent-status daemon + Nexus Altus worktrees |
| `sc_nats_bridge.py` | Fragile per-node transport → at-least-once JetStream envelopes | EdgeCitadel NATS pattern |

## Task lifecycle (A2A states)

```
submitted ──claim──▶ working ──▶ completed        (sc_done --result)
    ▲                   │  ▲          │
    │ retry          notif │ ack      ├─▶ failed   (sc_done --fail / no ack)
    └─(FAILED)          ▼  │          ├─▶ canceled
                  input-required      └─▶ rejected
```

- `working` / `input-required` / terminal states all count as **ack** — the
  spawn returns as soon as any of them is reached, no fixed sleep.
- `stop` (turn ended) is deliberately **not** completion — "finished a turn"
  and "finished the work" stay distinct, so a mid-task pause never looks done.
- Every transition is validated (illegal transitions raise `TransitionError`,
  fail closed) and appended to a hash-chained `events.jsonl`.

## Minimal usage

```python
import sys; sys.path.insert(0, r"C:\Users\techai\PKA testing\selfconnect")
from sc_spawn import spawn_agent, wait_for_completion

TASK_ROOT = r"C:\Users\techai\PKA testing\mesh-runs\demo"

res = spawn_agent(
    name="B",
    prompt="Refactor foo.py to remove the duplicate parser; add a test.",
    cwd=r"C:\Users\techai\some-repo",
    task_root=TASK_ROOT,
    worktree_from=r"C:\Users\techai\some-repo",  # optional: isolate the checkout
)
if res.ok:
    task = wait_for_completion(TASK_ROOT, res.task_id, timeout=1800)
    print(task.state.value, task.result)     # 'completed', '<agent summary>'
else:
    print("spawn failed / escalated:", res.detail)  # dead-lettered to Owner's Inbox
```

`spawn_agent` does, in order: budget gate (agent-status :8089) → optional git
worktree → create task → write briefing file → install hooks in the agent's
cwd → wait until the TUI shows an input prompt → ring the doorbell (one-line
injection pointing at the briefing) → wait for the ack. No ack after two
doorbells → the task is failed and dead-lettered to the Owner's Inbox.

## Reading a result without scraping

The `stop` hook records the agent's `transcript_path` in task meta. To read
the agent's last message losslessly:

```python
from sc_tasks import TaskBoard
from sc_transcript import last_assistant_message

task = TaskBoard(TASK_ROOT).get(task_id)
print(last_assistant_message(task.meta["transcript_path"]))
```

## Signed inter-node messages

```python
from sc_envelope import Envelope, load_or_create_mesh_key
key = load_or_create_mesh_key()                      # ~/.selfconnect/mesh.key
env = Envelope(sender="windows-a", recipient="spark-1",
               kind="task.dispatch", payload={"prompt": "..."},
               correlation_id=task_id).sign(key)
# ... transport (NATS or hub) ...
assert Envelope.from_json(raw).verify(key)           # forged/unsigned → False
```

Over NATS (optional, `pip install nats-py`):

```python
from sc_nats_bridge import MeshBus, run_sync
bus = run_sync(MeshBus(key).connect())
run_sync(bus.publish(env))          # JetStream at-least-once, awaits durable ack
```

## What stayed the same (on purpose)

- **Injection is still the last hop.** PostMessage/console I/O into the
  terminal is unchanged; the new layer only decides *when* to inject and
  *how* to confirm it landed. NATS carries envelopes, never model calls.
- **Billing is untouched.** Spawns use interactive `cmd.exe /k claude` in a
  dedicated conhost → subscription-billed, per the live-tested June finding.
  (Note: the June 15 `-p` credit split was later **paused** — re-test before
  relying on the distinction.)
- **The original SDK is not modified.** Everything here is additive; nothing
  in `self_connect.py` changed.

## Tests

```bash
python -m pytest tests/test_sc_tasks.py tests/test_sc_transcript.py \
  tests/test_sc_hooks.py tests/test_sc_envelope.py tests/test_sc_spawn.py -q
# 46 passed — plus a cross-process integration proof in the v0.12.0 session log
```

All Win32 is faked via a `_sc()` monkeypatch point, so the orchestration
tests run on any platform. NATS tests are import-guarded.
