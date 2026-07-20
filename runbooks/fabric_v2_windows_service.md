# Runbook: Fabric V2 Windows Service — Install, Operate, and Remove
<!-- selfconnect-runbook: {"status":"current","since":"2026-06-21","replacement":null,"entrypoint":false,"kind":"specialized"} -->

## What This Achieves

Runs SelfConnect Fabric V2 as a real Windows SCM (Service Control Manager) service:
- Registered in the Windows service database and visible in `services.msc`
- Auto-starts on boot via SCM without any user session required
- Restarts automatically on crash (configurable restart delay)
- Composes `FabricService` (host + router), writes PID/state files, restores
  replay state and queued mailbox payloads on restart, and runs watchdog checks
- Named pipe restricted to owner SID + SYSTEM (deny-all fallback); no raw SID
  appears in log output

Key files:
| File | Role |
|------|------|
| `sc_fabric_windows_svc.py` | pywin32 `ServiceFramework` wrapper — what SCM calls |
| `sc_fabric_service.py` | `FabricService` logic + install/remove/start/stop/query CLI |
| `tests/test_fabric_windows_svc.py` | Unit tests for the SCM wrapper |
| `tests/test_fabric_service.py` | Integration tests for the service logic |

---

## Prerequisites

1. **The service interpreter with SelfConnect and pywin32 installed system-wide:**
   ```
   python -m pip install --no-user ".[service]"
   python -m pywin32_postinstall -install
   ```
   `LocalSystem` cannot see packages installed only in a user's site directory.
   The post-install step registers the pywin32 service DLLs with Windows. Skip
   either system-scope installation or the DLL registration and the service can
   fail during import before it reaches SelfConnect.

2. **Admin rights** — install, remove, start, and stop all require an elevated prompt.
   Query and status checks do not require elevation.

3. **Both files present in the same directory:**
   - `sc_fabric_windows_svc.py`
   - `sc_fabric_service.py`

4. **Python on the system PATH** — SCM needs to find the interpreter when launching
   the service at boot. Verify with `where python` in an elevated prompt.

---

## Install

Run from an **elevated** (Run as Administrator) command prompt:

```
python sc_fabric_service.py install-service
```

Expected output:
```
Installing service SelfConnectFabricV2 ...
Service installed.
```

Alternative — start manually from `services.msc`:
1. Open `services.msc`
2. Find `SelfConnectFabricV2`
3. Right-click → Start

Or from an elevated prompt using `sc`:
```
sc start SelfConnectFabricV2
```

---

## Query Status

No elevation required:

```
python sc_fabric_service.py query-service
```

Example output:
```
SERVICE_NAME: SelfConnectFabricV2
  TYPE               : 10  WIN32_OWN_PROCESS
  STATE              : 4  RUNNING
  PID                : 7412
  FLAGS              :
```

Using the native `sc` tool:
```
sc query SelfConnectFabricV2
```

To check the PID file directly:
```
type %USERPROFILE%\.selfconnect\fabric_service.pid
```

---

## Stop and Remove

Stop the service (elevation required):
```
python sc_fabric_service.py stop-service
```

Remove the service from SCM (elevation required):
```
python sc_fabric_service.py remove-service
```

Both steps combined (stop first, then remove):
```
python sc_fabric_service.py stop-service && python sc_fabric_service.py remove-service
```

---

## State File Locations

| File | Default path |
|------|-------------|
| Replay/mailbox state | `%USERPROFILE%\.selfconnect\fabric_service_state.json` |
| PID file | `%USERPROFILE%\.selfconnect\fabric_service.pid` |

On restart, `FabricService` reads `fabric_service_state.json` before accepting new
connections — this restores replay-rejection state and any queued mailbox payloads
that had not been delivered before the previous shutdown.

To inspect the state file while the service is running:
```
type %USERPROFILE%\.selfconnect\fabric_service_state.json
```

---

## Troubleshooting

**"Access denied" on install or remove**
- Must run from an elevated prompt (Run as Administrator).
- UAC prompt suppression is not supported — elevation is required by SCM.

**Service fails to start — no error shown**
- Open Event Viewer → Windows Logs → Application
- Filter by Source: `SelfConnectFabricV2` or `Python`
- Common causes: `pywin32_postinstall.py` not run, Python not on PATH, missing
  `sc_fabric_service.py` next to `sc_fabric_windows_svc.py`.

**Named pipe not found after service starts**
- Verify no other instance is running: `python sc_fabric_service.py query-service`
- If STATE is STOPPED, check Event Viewer for the startup error.
- Only one instance of the named pipe host can run at a time.

**`win32service` import error at runtime**
```
pip install pywin32
python Scripts/pywin32_postinstall.py -install
```
Then restart the elevated prompt and retry.

**Service installed but not visible in services.msc**
- SCM database refresh can be slow. Press F5 in services.msc.
- Confirm install succeeded: `sc query SelfConnectFabricV2`

**State file missing on restart**
- The state directory (`%USERPROFILE%\.selfconnect\`) is created automatically on
  first run. If permissions were changed, recreate it manually:
  ```
  mkdir %USERPROFILE%\.selfconnect
  ```

---

## Known Boundaries

- **TPM PASS artifact** requires a supported dTPM. fTPM returns `0x80090026`
  (NTE_NOT_SUPPORTED) — this is a hardware limitation, not a service bug.
- **MSI signing** requires Azure Artifact Signing or SignPath Foundation;
  self-signed MSIs trigger SmartScreen on other machines.
- **Gemini 10/15/20 quota** for any Fabric mesh route that calls the Gemini API
  requires a Vertex AI API key; the oauth-personal flow reached EOL on 2026-06-18.
- **Session 0 isolation**: The service runs in Windows Session 0 (no desktop).
  Any code path that calls `wt.exe`, `SendInput`, or `SetForegroundWindow` will fail
  silently inside the service — keep those calls in user-session helpers only.

---

## Verified

- 2026-07-20 — Installed as the auto-start `SelfConnectFabricV2` LocalSystem
  service on the release workstation. A real named-pipe ACK completed, replay
  rejection survived graceful restart, forced process termination triggered SCM
  recovery with a new PID, and a post-recovery roundtrip completed. Recovery
  delays are 5/15/60 seconds with a one-day reset period. Evidence:
  `experiments/fabric_v2/results/fabric_v2_scm_live_20260720_redacted.json`.
- Session 15 (2026-06-21) — `SelfConnectFabricV2` SCM service install/start/stop/remove
  proved; pywin32 `ServiceFramework` wrapper confirmed; state restore and watchdog checks
  passed; DACL hardening (owner SID + SYSTEM, deny-all fallback) confirmed in pipe layer.
- Evidence artifacts: `experiments/fabric_v2/results/fabric_v2_service_selftest_20260621_113419_redacted.json`
