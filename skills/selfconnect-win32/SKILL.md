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
- For MCP, `send_text` must remain disabled unless `SELFCONNECT_MCP_ALLOW_INPUT=1`.
- Keep `WM_CHAR` routing as a fallback until a sidecar control plane is proven.
- Keep TPM, DACL pipe, ETW, MSIX, and service-mode work optional by default.

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
- `send_text`

Enable MCP input only for controlled tests:

```powershell
$env:SELFCONNECT_MCP_ALLOW_INPUT = "1"
selfconnect-mcp
```

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
