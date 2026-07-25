# Capability OS installation and migration

The Capability OS release candidate remains disabled by default. Installation
does not grant window input, file writes, command execution, MCP credentials,
or skill approval.

## Install

On the supported interactive Windows host:

```powershell
python -m pip install "selfconnect[capability-os]"
selfconnect doctor --json
selfconnect-capabilities list
selfconnect-capabilities verify-evidence
```

For a source checkout:

```powershell
python -m pip install -e ".[capability-os]"
```

## Profiles

The operator selects `SC_CAPABILITY_GOVERNANCE_PROFILE`:

- `observe` is the compatibility profile. Capability OS components remain
  separately feature-flagged.
- `governed` requires the kernel, progressive skills, durable tasks, shadow
  learning, and the visual specialist. Missing components abort startup.
- `restricted` adds a hard prohibition on window input, writes, commands, and
  external MCP configuration.

The complete governed release-candidate configuration is:

```powershell
$env:SC_CAPABILITY_KERNEL = "1"
$env:SC_DYNAMIC_SKILLS = "1"
$env:SC_TASK_GRAPH = "1"
$env:SC_SKILL_LEARNING = "shadow"
$env:SC_VISUAL_SPECIALIST = "1"
$env:SC_CAPABILITY_GOVERNANCE_PROFILE = "governed"
```

Mutation flags remain independent and default off. The model cannot change
the selected profile or its immutable launch authority.

## State migration

Capability state contains a DPAPI-protected integrity key. Migration is
therefore same-Windows-user by default. Copying the blob to another account or
machine is not a key migration and must fail authentication.

First stop writers and produce a read-only plan:

```powershell
selfconnect-capabilities verify-evidence
selfconnect-capabilities-migrate `
  --source "$env:LOCALAPPDATA\SelfConnect\capabilities" `
  --destination "D:\SelfConnect\capabilities-v1"
```

Review every listed path, digest, byte count, evidence record count, and head
hash. Apply only to a new or empty destination:

```powershell
selfconnect-capabilities-migrate `
  --source "$env:LOCALAPPDATA\SelfConnect\capabilities" `
  --destination "D:\SelfConnect\capabilities-v1" `
  --apply
```

The utility:

1. authenticates the source evidence chain;
2. excludes lock and temporary files;
3. copies into a unique staging directory;
4. compares every staged file digest;
5. reopens the staged DPAPI key and re-verifies the evidence head;
6. atomically renames the stage into a previously empty destination.

It never merges evidence chains. Retain the source until the destination
passes `verify-evidence`, task recovery, world-state reads, and an application
rollback checkpoint. Rollback means stop writers and restore the untouched
source path; do not splice files between state roots.
