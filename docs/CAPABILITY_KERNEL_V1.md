# SelfConnect Capability Kernel v1

Capability Kernel gives local models progressive skill discovery without
granting them direct authority over Python imports, commands, MCP servers, or
raw window handles.

It is disabled by default and additive to the existing local-agent runtime.

## Architecture

```text
Local model
  -> capability_discover
  -> capability_inspect
  -> capability_execute
       |
       v
SkillRegistry -> Authority -> CapabilityBroker -> trusted host adapter
                                      |
                                      v
                               independent verifier
                                      |
                                      v
                            hash-linked evidence store
```

Only trusted host code can register an adapter. A JSON skill manifest names an
adapter but cannot contain an executable, command, Python import, or MCP
endpoint. External manifests must carry a matching SHA-256 content digest.

## Enable a supervised local-agent session

```powershell
$env:SC_CAPABILITY_KERNEL = "1"
$env:SC_DYNAMIC_SKILLS = "1"
$env:SC_TASK_GRAPH = "1"
$env:SC_SKILL_LEARNING = "shadow"

selfconnect-local-agent --role local-qwen-capability-1 --model qwen3.6:27b
```

`SC_DYNAMIC_SKILLS=1` replaces the full native tool catalog on ordinary chat
turns with three meta-tools:

- `capability_discover`
- `capability_inspect`
- `capability_execute`

Controller-supplied `ToolContract` workflows remain able to expose an exact
native-tool allowlist. Existing launches are unchanged when the kernel flag is
absent.

## Permissions

Authority is immutable for a runtime instance. Read permissions are granted to
the local SelfConnect runtime; mutation permissions are derived from the
existing independent gates:

| Capability permission | Existing gate |
|---|---|
| `input.window` | `SC_LOCAL_AGENT_ALLOW_INPUT=1` |
| `write.file` | `SC_LOCAL_AGENT_ALLOW_WRITES=1` |
| `execute.command` | `SC_LOCAL_AGENT_ALLOW_COMMANDS=1` |

A model cannot enable these permissions through a tool call, skill manifest, or
prompt.

## External skill manifests

Set `SC_CAPABILITY_SKILL_PATHS` to one or more directories separated by the
platform path separator. Every `*.json` file is validated strictly and must
include a matching `manifest_digest`.

Manifests describe:

- stable name and version;
- human/model description and tags;
- trusted adapter identifier;
- required permissions;
- strict input and output contracts;
- named verification checks;
- provenance and content digest.

External skills are discoverable only after their adapter has been registered
by trusted host code. This deliberately prevents a manifest from becoming an
arbitrary-code plugin.

## Durable task graphs

`CapabilityKernel.new_task()` creates an atomically checkpointed graph.
Dependencies must reference existing earlier steps, preventing cycles. Steps
move through explicit states:

```text
pending -> running -> completed | failed | blocked
blocked | failed -> pending | cancelled
```

`run_ready()` executes only dependency-ready steps, persists every transition,
and links the task transition to the capability evidence ID. A new process can
load the task and continue from the checkpoint.

## Evidence

Capability execution, permission denial, and task transitions are written to a
hash-linked JSONL evidence chain. Secret-like fields and file `content` values
are redacted before persistence. `selfconnect-capabilities verify-evidence`
validates the chain.

The v1 chain is tamper-evident, not tamper-resistant. Production deployments
should anchor the head hash to the existing TPM or off-host/WORM evidence path.

## World state

The kernel stores structured observations with:

- stable keys;
- authoritative source names;
- confidence;
- observation and expiration timestamps;
- current/stale status;
- value digests;
- sensitive-value replacement before persistence;
- a durable change feed.

The local-agent runtime seeds a fresh `runtime.<role>` observation when the
kernel is enabled. Trusted adapters can refresh runtime, mesh, and platform
state. Models receive read-only state access through the
`selfconnect.world-state` capability; they cannot write observations.

Additional bounded collectors cover visible-window identity fields, mesh roles,
process names/status, Windows service status, NVIDIA GPU resources, and
SelfConnect platform capabilities. Missing or expired prefixes are refreshed
once through a trusted host callback. Process command lines, executable paths,
raw window text, environment variables, and credentials are intentionally not
collected.

## Inspection CLI

```powershell
selfconnect-capabilities list
selfconnect-capabilities discover "read a terminal window"
selfconnect-capabilities inspect selfconnect.read-window
selfconnect-capabilities state --prefix runtime.
selfconnect-capabilities changes --since 0
selfconnect-capabilities verify-evidence
```

The CLI is read-only in v1. It does not register execution adapters.

## v1 boundaries

- Skill learning supports only `off` and `shadow`. It cannot publish or execute
  generated skills.
- Manifest digests prove content identity, not publisher identity. A later
  release can bind manifests to existing SelfConnect signing identities.
- MCP adapter discovery, a visual-specialist router, automatic plan synthesis,
  and sandboxed skill replay are future layers.
- Independent verification is adapter-specific. Missing named verifiers fail
  closed.
