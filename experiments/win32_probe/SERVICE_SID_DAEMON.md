# SelfConnect Service SID Daemon Mode

Date: 2026-06-16

Goal: move long-running policy/control behavior into an OS-owned identity
without running the whole user-facing agent surface elevated.

## What This Proves

The useful Windows primitive is the service identity:

- service process identity separate from the interactive user.
- service SID such as `NT SERVICE\SelfConnectProbe`.
- object ACLs scoped to that service SID.
- Service Control Manager restart/control surface.

This is a control-plane hardening path. It should not replace the working
terminal transport until the service, pipe ACLs, and user-session bridge are
proven.

## What This Does Not Prove Yet

`service_sid_probe.py` is not a Windows service implementation. A real service
payload needs one of:

- a native service executable.
- a Python service wrapper such as pywin32.
- a supervised wrapper such as NSSM for experiments only.

The helper only wraps `sc.exe` commands for install/query/delete/sidtype.

## Probe Commands

Query a service:

```powershell
python experiments\win32_probe\service_sid_probe.py query --name SelfConnectProbe
```

Print the service SID name:

```powershell
python experiments\win32_probe\service_sid_probe.py sid --name SelfConnectProbe
```

Create a service around a real service binary or wrapper:

```powershell
python experiments\win32_probe\service_sid_probe.py install --name SelfConnectProbe --bin-path "C:\path\to\service.exe"
```

Enable a restricted service SID:

```powershell
python experiments\win32_probe\service_sid_probe.py sidtype --name SelfConnectProbe --sid-type restricted
```

Delete the probe service:

```powershell
python experiments\win32_probe\service_sid_probe.py delete --name SelfConnectProbe
```

## Target Architecture

1. User-facing terminal/CLI/MCP tools stay unelevated.
2. Policy daemon runs as `SelfConnect` service with service SID enabled.
3. User tools talk to the daemon over a DACL-restricted named pipe.
4. Privileged actions go through a narrow broker and are separately audited.
5. The daemon emits ETW events and writes signed/hash-chained audit records.

## Open Questions

- Whether the first service payload should be native C++ or pywin32.
- Whether per-user install remains the default and service mode is an optional
  advanced profile.
- How to bridge from Session 0 service context into the active user session
  without violating least privilege.
- Which objects should be ACLed to `NT SERVICE\SelfConnect`.
