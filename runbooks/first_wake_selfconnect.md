# Runbook: First-Wake SelfConnect Agent Launch
<!-- selfconnect-runbook: {"status":"current","since":"2026-07-05","replacement":null,"entrypoint":true} -->

## What
Bring SelfConnect into a known-good state at the start of a session, then open and
contact Claude, Codex, Gemini, or local-model terminals without rediscovering launch
quirks.

## Prerequisites
- Interactive Windows desktop session.
- SelfConnect installed from `C:\Users\techai\PKA testing\selfconnect`.
- The target CLI is installed and discoverable with `Get-Command`, or its full path is known.
- No input is sent until the target HWND, PID, exe, class, and title are verified.

## First-Wake Order

1. Read this runbook first for SelfConnect launch/contact work.
2. Query MemoryWeb only for fresh context after reading this runbook:
   ```powershell
   python D:\memory-web\mw_query.py "SelfConnect first wake terminal launch and current session context"
   ```
3. Enter the repo and check package/mesh health:
   ```powershell
   Set-Location -LiteralPath "C:\Users\techai\PKA testing\selfconnect"
   git status --short --branch
   python -m pip show selfconnect
   selfconnect doctor --json
   selfconnect-mesh list
   ```
4. Read `agent_launch_registry.md` before launching. It is the canonical place
   for per-target CLI flags, startup waits, and submit quirks.
5. Confirm the target CLI:
   ```powershell
   Get-Command claude
   Get-Command codex
   Get-Command gemini
   Get-Command ollama
   ```

## Reliable Launch Pattern

Before using any launch template below, check `agent_launch_registry.md` for a
newer target-specific recipe. CLI flags change, and the registry records the
latest verified command per target.

Prefer launching a real shell with `Start-Process powershell.exe`, then invoking the
agent inside that shell. Do not ask `wt.exe` to parse a complex call expression.

Claude, verified 2026-07-05:
```powershell
$repo = "C:\Users\techai\PKA testing\selfconnect"
$claude = "C:\Users\techai\AppData\Roaming\npm\claude.ps1"
$title = "SC Claude Worker"
$cmd = "`$Host.UI.RawUI.WindowTitle = '$title'; Set-Location -LiteralPath '$repo'; & '$claude'"

Start-Process -FilePath powershell.exe `
  -WorkingDirectory $repo `
  -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $cmd)
```

Codex template:
```powershell
$repo = "C:\Users\techai\PKA testing\selfconnect"
$title = "SC Codex Worker"
$cmd = "`$Host.UI.RawUI.WindowTitle = '$title'; Set-Location -LiteralPath '$repo'; codex"

Start-Process -FilePath powershell.exe `
  -WorkingDirectory $repo `
  -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $cmd)
```

Gemini template:
```powershell
$repo = "C:\Users\techai\PKA testing\selfconnect"
$title = "SC Gemini Worker"
$cmd = "`$Host.UI.RawUI.WindowTitle = '$title'; Set-Location -LiteralPath '$repo'; gemini"

Start-Process -FilePath powershell.exe `
  -WorkingDirectory $repo `
  -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $cmd)
```

Local Ollama template:
```powershell
$repo = "C:\Users\techai\PKA testing\selfconnect"
$title = "SC Local Ollama"
$model = "llama3.1"
$cmd = "`$Host.UI.RawUI.WindowTitle = '$title'; Set-Location -LiteralPath '$repo'; ollama run $model"

Start-Process -FilePath powershell.exe `
  -WorkingDirectory $repo `
  -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $cmd)
```

## Discovery And Verification

After launch:
```powershell
Start-Sleep -Seconds 8
selfconnect windows --query SC --json
selfconnect windows --query Claude --json
selfconnect windows --query Codex --json
selfconnect windows --query Gemini --json
```

Window titles may change after the agent starts. Claude commonly becomes
`Claude Code`. Use the discovered HWND and verify before input:
```powershell
selfconnect guard --hwnd <HWND> `
  --expect-pid <PID> `
  --expect-exe WindowsTerminal.exe `
  --expect-class CASCADIA_HOSTING_WINDOW_CLASS `
  --expect-title "<TITLE SUBSTRING>"
```

Read the terminal and confirm it is at a prompt:
```powershell
selfconnect read --hwnd <HWND>
```

## Mesh Registration

Register only active, verified windows. Use unique roles and keep the returned
`birth_id` in status reports.

```powershell
selfconnect-mesh register `
  --role <unique-role> `
  --hwnd <HWND> `
  --agent <claude|codex|gemini|local_model> `
  --profile explore `
  --status active `
  --task "first contact only; no delegated work" `
  --expect-pid <PID> `
  --expect-exe WindowsTerminal.exe `
  --expect-class CASCADIA_HOSTING_WINDOW_CLASS `
  --expect-title "<TITLE SUBSTRING>"
```

Record the handoff:
```powershell
selfconnect-mesh event --type task_assigned --role <unique-role> --summary "First-contact prompt only"
```

## Safe Contact

Send input only after successful guard/read/registration:
```powershell
selfconnect send --hwnd <HWND> `
  --text "First contact from Codex via SelfConnect. Please reply with one concise line confirming you can read this. Do not make code changes or run commands." `
  --submit `
  --allow-input `
  --expect-pid <PID> `
  --expect-exe WindowsTerminal.exe `
  --expect-class CASCADIA_HOSTING_WINDOW_CLASS `
  --expect-title "<TITLE SUBSTRING>"
```

Then read the reply:
```powershell
Start-Sleep -Seconds 20
selfconnect read --hwnd <HWND>
```

Close out:
```powershell
selfconnect-mesh event --type task_completed --role <unique-role> --summary "First contact confirmed"
selfconnect-mesh update --role <unique-role> --status standby --task "first contact complete; no delegated work"
```

## Known Failures

- `wt new-tab ... claude` can fail with `0x80070002` because `claude` may not resolve
  inside the new terminal launch environment.
- `wt new-tab ... "& '...\claude.ps1'"` can fail because Windows Terminal may treat
  the PowerShell call expression as the executable name.
- A requested window title can change after startup. Always discover the real title
  with `selfconnect windows --json` before calling `guard` or `send`.
- Do not kill by WindowsTerminal PID. Windows Terminal can share one process across
  many tabs.

## Verified
- 2026-07-05: Claude Code launched via `Start-Process powershell.exe`, discovered as
  HWND `31789242`, registered as `claude-first-contact-1`, and replied:
  "Confirmed - SelfConnect message received loud and clear."
