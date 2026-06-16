---
name: selfconnect-win32
description: Operate, package, and test SelfConnect on Windows. Use when working with SelfConnect's Win32 transport, UIA text reads, PrintWindow capture, named-pipe control plane, TPM or identity probes, package installation, MCP server adapter, or cross-machine validation of SelfConnect capabilities.
---

# SelfConnect Win32

Use this skill to keep SelfConnect work reproducible across Windows machines.
Prefer package commands and capability probes before writing one-off snippets.

## Start Here

1. Check the branch and package state:

```powershell
git status --short --branch
python -m pip show selfconnect
selfconnect doctor --json
python -m sc_cli guard --hwnd 0x123456 --expect-pid 1234 --expect-class CASCADIA_HOSTING_WINDOW_CLASS
selfconnect-mesh list
```

2. If running from source instead of an installed wheel:

```powershell
python -m sc_cli doctor --json
python -m sc_cli windows --query Claude
```

3. If installing from GitHub:

```powershell
pip install "selfconnect[full,mcp] @ git+https://github.com/rblake2320/selfconnect.git@test/win32-hardening-v1"
```

## Adapter Choice

Use this waterfall for read paths:

1. UIA `TextPattern` / `TextChanged` event.
2. UIA text polling.
3. child-window text via `WM_GETTEXT`.
4. `PrintWindow` capture.
5. OCR or visual inspection.

Use this separation for message paths:

1. User-visible terminal input: `WM_CHAR` / `send_string`.
2. Agent routing, migration, peer registry, or metadata: named pipe or file registry.
3. Identity-sensitive control plane: named pipe with DACL and `ImpersonateNamedPipeClient`.
4. Hardware-backed identity: TPM/CNG adapter when available.

## Safety Rules

- Do not type into windows until the target HWND, PID, title, exe, and class are checked.
- Use `selfconnect send` only with `--allow-input` or `SELFCONNECT_ALLOW_INPUT=1`.
- Use `selfconnect send` with `--expect-pid`, `--expect-exe`, `--expect-class`, or
  `--expect-title`; otherwise it fails closed unless `--confirm-current-target`
  is provided after inspection.
- Terminal classes are required by default. Use `--allow-non-terminal` only when
  deliberately testing a non-terminal target.
- For MCP, `send_text` must remain disabled unless `SELFCONNECT_MCP_ALLOW_INPUT=1`.
- For MCP, call `verify_target` before `send_text`, and pass the same expected
  target fields into `send_text`. Keep `require_terminal=true` unless a
  non-terminal target is intentional.
- Keep `WM_CHAR` routing as a fallback until a sidecar control plane is proven.
- Keep TPM, DACL pipe, ETW, MSIX, and service-mode work optional by default.

## Spawning And Mesh Discipline

- When spawning Codex, set its permissions profile first if possible; otherwise
  monitor approvals and approve/deny deliberately.
- Claude sessions may already have the user's approval profile configured; still
  register the spawned window before assigning work.
- Gemini sessions may need approval monitoring.
- Always identify the new HWND, PID, title, exe, and class before injecting a
  handoff.
- Do not assume every terminal is in the mesh. Register only active mesh windows:

```powershell
selfconnect-mesh scan --query Codex
selfconnect-mesh register --role codex-2 --hwnd 0x123456 --agent codex --task "specific task" --expect-class CASCADIA_HOSTING_WINDOW_CLASS
```

- Roles must be unique. Do not create multiple `B` roles; use names like
  `codex-1`, `claude-1`, `rmc-1`, `gemini-1`, or task-specific roles.
- On migration or successor spawn, register the new role or use `--replace` for
  the migrated role.
- Before idle or after auto-compact, run:

```powershell
selfconnect-mesh update --role codex-1 --status compacting --task "waiting for resume"
selfconnect-mesh heartbeat --role codex-1
selfconnect-mesh list
```

- Do not run registry writes in parallel; `selfconnect-mesh` uses an atomic JSON
  file, not a locking database.

## MCP Server

Run the server:

```powershell
selfconnect-mcp
```

Available tools:

- `doctor`
- `list_windows`
- `read_window`
- `capture_window`
- `verify_target`
- `send_text`

Enable MCP input only for controlled tests:

```powershell
$env:SELFCONNECT_MCP_ALLOW_INPUT = "1"
selfconnect-mcp
```

Treat `capabilities` as platform probes. For example, `tpm_identity=true` and
`named_pipe_impersonation=true` mean the machine appears to support those
primitives; the core SDK path may still use Ed25519 software identity while
enterprise or experiment paths use TPM/DACL impersonation.

## Validation

Before publishing or moving to another machine:

```powershell
python -m ruff check sc_cli.py sc_mcp.py self_connect.py tests/test_package_adapters.py
python -m py_compile sc_cli.py sc_mcp.py self_connect.py
python -m pytest tests/test_package_adapters.py tests/test_win32_hardening.py -q
python -m build
```

Check the built wheel includes:

- `_win32_abi.py`
- `self_connect.py`
- `sc_cli.py`
- `sc_mcp.py`
- `sc_identity.py`
- `sc_firewall.py`
- `sc_reliability.py`
- `sc_pq.py`
- `sc_shell.py`
- `sc_resume.py`
