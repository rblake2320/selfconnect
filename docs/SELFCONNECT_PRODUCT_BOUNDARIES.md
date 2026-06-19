# SelfConnect Product Boundaries

Last updated: 2026-06-19

This note exists to prevent capability sprawl. SelfConnect has become an
ecosystem, but not every proof belongs in the default runtime path.

## Product Thesis

SelfConnect is an OS-native AI peer mesh. It uses the operating system's own
desktop, accessibility, process, and IPC primitives to let agents communicate,
act, and verify delivery without requiring a cloud API, browser extension, CDP,
WebDriver, or MCP control path.

That does not mean the ecosystem never uses MCP, SSH, S3, email, or HTTP. It
means the core actuation proofs do not require those layers.

## Three Profiles

| Profile | Audience | Default posture | Belongs here |
| --- | --- | --- | --- |
| Normal / explore | Ron and everyday local testing | Fast, low-friction, target-guarded | `WM_CHAR`, UIA readback, echo filter, mesh registry, birth IDs, local-model mailbox |
| Enterprise / governed | companies and controlled teams | Same capabilities with policy wrappers | role leases, named-pipe identity, service mode, ETW, audit, MCP adapter, installer |
| Government / high assurance | IL5/IL6-minded deployments | fail-closed, off-host evidence, hardware identity where available | WORM, TPM/CNG, job sandbox, ATO docs, strict audit, service SID |

Target verification is not governance overhead. It stays on in every profile
because it prevents wrong-window writes.

## Core Runtime Boundary

Core should stay small and boring:

- `self_connect.py`: Win32 primitives, capture, send, read helpers.
- `_win32_abi.py`: central Win32 ABI definitions.
- `sc_cli.py`: package-safe command line access.
- `sc_mesh_registry.py`: roles, birth IDs, generations, handoffs, health.
- `sc_echo_filter.py`: local echo versus external output classification.
- `sc_local_model_role.py`: durable local-model mailbox identity.
- `sc_mcp.py`: optional adapter surface; input remains explicitly gated.

Core should not absorb every experiment. A capability graduates only when it has:

1. a repeatable test or probe;
2. a redacted artifact if it was live;
3. a failure plan;
4. a clear profile placement;
5. no dependency that slows normal mode unless explicitly enabled.

## Experimental Boundary

Keep these under `experiments/` or docs until they are deliberately promoted:

- TPM attestation variants;
- ETW provider details;
- service SID daemon probes;
- browser-specific UIA probes;
- chained-channel compositions;
- Spark/Linux desktop experiments;
- local-model visible demos;
- vendor-specific app controllers.

Experiments can support patent evidence without becoming product defaults.

## Patent Claim Discipline

The strongest SelfConnect claim is the composition:

```text
agent/local-model action source
  -> action validator
  -> target guard
  -> surface-specific channel router
  -> OS-native actuation
  -> structured readback
  -> echo filter
  -> durable role identity / audit evidence
```

Do not claim:

- SelfConnect never uses MCP/API/SSH/S3;
- SelfConnect is a CAPTCHA bypass tool;
- every experimental probe is production-ready;
- every AWS/Spark/AI Army document is SelfConnect-specific evidence.

Do claim, when supported by the relevant artifact:

- the actuation path can run without MCP, CDP, browser extension, WebDriver, or
  external API;
- terminal and browser surfaces require different native channels;
- wrong-target writes fail closed when target guard expectations do not match;
- local echo is separated from external peer output;
- role identity survives terminal migration through birth ID and generation;
- local models can select constrained actions that SelfConnect validates before
  execution.

## Bloat Tripwires

Treat a change as bloat unless it answers at least one of these:

- makes normal mode faster, clearer, or more reliable;
- gives enterprise/government a needed control without slowing normal mode;
- improves patent evidence traceability;
- removes ambiguity from agent coordination;
- makes failure behavior explicit.

If a feature does not pass that test, park it in `experiments/`, document the
finding, and leave the default runtime alone.

