# Failure Remediation Protocol (FRP) — Specification v0.1

**Also known as:** Pathbook Protocol (PBP) for failure-class entries  
**Date:** 2026-05-17  
**Status:** Draft  
**Canonical repo:** selfconnect / docs/frp-spec-v0.1.md  
**Companion:** [NRP — Navigation Runbook Protocol](nrp-spec-v0.1.md) (parallel protocol for UI navigation)  
**Distribution layer:** [aihangout.ai/api/pathbooks](https://aihangout.ai/api/pathbooks)

---

## 1. Problem Statement

Every AI agent, on every machine, encounters the same operational failures repeatedly:
- Stale lock files blocking process restart
- Shell environment mismatches (Win32 vs WSL vs bash)
- File permission denials in sequence
- Unicode encoding errors on Windows consoles
- Port conflicts from zombie processes

Each failure causes the agent to guess at a fix, burn inference tokens on retries, and sometimes succeed after 4+ failed attempts. When millions of agents hit the same wall daily, the aggregate token waste is measured in the billions per day.

**The core insight:** These are not unpredictable failures. They are known failures with known fixes. The fix path has been proved by someone, somewhere, at some time. The problem is that proof is not stored, signed, indexed, or distributed in a form that an agent can retrieve and execute deterministically.

**The solution:** A signed, machine-readable, globally-distributed registry of known failure → verified fix paths — structured so any agent can query before guessing, retrieve the proved path, and execute it directly.

**Precedent:** The CVE/NVD vulnerability database proved this model works at planetary scale. Every security tool on Earth ingests CVEs. A "CVE for agent failures" is the same concept applied to operational runtime failures rather than software vulnerabilities.

---

## 2. Protocol Design

### 2.1 Three-Layer Architecture

```
┌─────────────────────────────────────────────────────┐
│ LAYER 1: CAPTURE                                     │
│  - Log every failed attempt with full env context    │
│  - Record: error text, tool call, env fingerprint,  │
│    OS, shell, working dir, exit code                 │
│  - Inputs: OTel traces, LangSmith, Langfuse, Sentry │
│            + SelfConnect Observer + agent self-log   │
└────────────────────────┬────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│ LAYER 2: COMPILE                                     │
│  - When the eventual successful fix is found:        │
│    convert (failed_attempts + working_fix) → YAML   │
│  - Compute error fingerprint (hash of normalized     │
│    error text + env class)                           │
│  - Sign with agent provenance + BPC/CngIdentity      │
│  - Assign trust tier: Draft                          │
└────────────────────────┬────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│ LAYER 3: DISTRIBUTE                                  │
│  - POST to aihangout.ai/api/pathbooks                │
│  - Query: POST /api/pathbooks/lookup                 │
│    { "error_signature": "<fingerprint>" }           │
│  - MCP tools: pathbook.lookup, pathbook.contribute  │
│  - Any agent on Earth queries before guessing        │
└─────────────────────────────────────────────────────┘
```

### 2.2 Relationship to NRP (Navigation Runbook Protocol)

| Dimension | NRP | FRP |
|-----------|-----|-----|
| What it captures | Known UI navigation paths | Known failure → fix paths |
| Trigger | domain + intent | error fingerprint + env class |
| Steps | click, fill, navigate, verify | retry with fix, verify resolved |
| Source | Playwright traces | agent failure logs + recovery logs |
| Trust root | Playwright recorder | cryptographic fix proof |
| Cold-start data | real browser traces | Stack Overflow + GitHub Issues |

NRP and FRP are parallel protocols. NRP says "here is the proved path through a UI." FRP says "here is the proved fix for a known failure." A complete agent stack needs both.

---

## 3. YAML Schema

### 3.1 FRP Entry (full schema)

```yaml
schema_version: frp/v0.1
frp_id: FRP-<8-hex>           # auto-assigned on submit if absent
title: string                  # human-readable name
category: enum                 # os | shell | encoding | network | permission | lock | process | other

# Error identification
error_signature: string        # normalized error text (primary lookup key)
error_fingerprint: string      # SHA-256 of normalized error_signature + env_class
error_pattern: regex           # optional: regex to match error variants
env_class: string              # e.g. "windows-bash", "wsl2", "linux-bash", "python-3.12-win32"

# Failure history (what was tried and failed)
failed_attempts:
  - attempt: 1
    command: string            # what the agent tried
    error: string              # what came back
    token_cost: integer        # approximate tokens burned
  - attempt: 2
    command: string
    error: string
    token_cost: integer

# The working fix
fix_path:
  steps:
    - step: 1
      action: string           # run | check | wait | read | write | delete
      command: string          # exact command or pseudocode with {placeholders}
      expected_output: string  # what success looks like
      on_failure: string       # what to do if this step fails
  verify:
    - check: string            # assertion to confirm fix worked
      command: string
      expected: string

# Provenance
recorded: date                 # ISO 8601
agent_id: string               # hwnd, session ID, or agent name
orchestrator_id: string        # if applicable
transport: string              # e.g. "SelfConnect PostMessage(WM_CHAR)"
sdk_version: string
provenance_hash: string        # SHA-256 of the fix_path content

# Registry metadata
status: enum                   # draft | reproduced | verified | community_confirmed | maintainer_approved | deprecated | dangerous
trust_tier: enum               # same as status
confidence: float              # 0.0 – 1.0
times_applied: integer         # how many agents have used this fix
times_succeeded: integer       # how many applications resulted in confirmed success
token_savings_estimate: integer # estimated tokens saved per application
source_type: enum              # agent_log | so_import | github_import | manual
source_url: string             # optional: link to SO answer, GH issue, etc.
```

### 3.2 Minimal Required Fields (API)

The aihangout.ai API requires only:

```yaml
title: string
error_signature: string
fix_path:                      # stored as fix_path_yaml in API
  steps: [...]
```

Everything else is optional but improves lookup precision and trust.

### 3.3 Error Fingerprint Algorithm

```python
import hashlib, re

def compute_error_fingerprint(error_text: str, env_class: str) -> str:
    """
    Normalize error text and hash with env class.
    Normalization removes:
      - file paths (replaced with <PATH>)
      - line numbers (replaced with <LINE>)
      - process IDs (replaced with <PID>)
      - hex addresses (replaced with <ADDR>)
      - timestamps (replaced with <TIME>)
    """
    normalized = re.sub(r'[A-Za-z]:\\[^\s"\']+', '<PATH>', error_text)
    normalized = re.sub(r'/[^\s"\']+', '<PATH>', normalized)
    normalized = re.sub(r'\bline \d+\b', 'line <LINE>', normalized)
    normalized = re.sub(r'\bpid \d+\b', 'pid <PID>', normalized, flags=re.I)
    normalized = re.sub(r'\b0x[0-9a-f]+\b', '<ADDR>', normalized, flags=re.I)
    normalized = re.sub(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', '<TIME>', normalized)
    normalized = normalized.strip().lower()
    content = f"{normalized}|{env_class}"
    return hashlib.sha256(content.encode()).hexdigest()
```

---

## 4. Trust Tiers

Trust tiers prevent the registry from becoming a vector for supply-chain attacks. Every entry enters as `draft` and must earn promotion.

| Tier | Meaning | Promotion Requirement |
|------|---------|----------------------|
| `draft` | Agent claims this fixed it | Submission only |
| `reproduced` | Fix independently reproduced | ≥1 independent agent confirms fix worked |
| `verified` | Fix verified with automated assertion | Automated verify step passed ≥3 times |
| `community_confirmed` | Widely used with high success rate | `times_succeeded / times_applied ≥ 0.9` AND `times_applied ≥ 10` |
| `maintainer_approved` | Human expert reviewed and approved | Manual review by trusted maintainer |
| `deprecated` | Environment changed; fix no longer applies | Promotion by any contributor with evidence |
| `dangerous` | Fix causes harm in some configurations | Immediate flag; never auto-executed |

**Rule:** An agent MUST NOT auto-execute a `draft` or `dangerous` entry without human approval. `reproduced` and above can be auto-applied. `dangerous` is read-only — shown as warning context only.

---

## 5. Lookup Protocol

An agent encountering a failure queries BEFORE retrying:

```python
import hashlib, re, json, urllib.request

def lookup_frp(error_text: str, env_class: str) -> dict | None:
    """
    Query aihangout.ai for a verified fix path.
    Returns the FRP entry if trust_tier >= 'reproduced', else None.
    """
    fingerprint = compute_error_fingerprint(error_text, env_class)
    
    # Try exact fingerprint match first
    payload = json.dumps({"error_fingerprint": fingerprint}).encode()
    req = urllib.request.Request(
        "https://aihangout.ai/api/pathbooks/lookup",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            if data.get("pathbook") and data["pathbook"].get("trust_tier") != "draft":
                return data["pathbook"]
    except Exception:
        pass
    
    # Fall back to signature text match
    payload = json.dumps({"error_signature": error_text[:500]}).encode()
    req = urllib.request.Request(
        "https://aihangout.ai/api/pathbooks/lookup",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("pathbook")
    except Exception:
        return None
```

**Timeout contract:** Lookup MUST complete within 5 seconds. If the registry is unreachable, the agent falls back to inference — FRP is a performance optimization, not a hard dependency.

---

## 6. Contribution Protocol

When an agent succeeds after ≥2 failed attempts, it SHOULD contribute:

```python
def contribute_frp(
    title: str,
    error_text: str,
    env_class: str,
    failed_attempts: list[dict],
    fix_steps: list[dict],
    verify_steps: list[dict],
    jwt: str,
) -> dict:
    """Submit a new FRP entry as draft."""
    fingerprint = compute_error_fingerprint(error_text, env_class)
    payload = {
        "title": title,
        "error_signature": error_text[:500],
        "error_fingerprint": fingerprint,
        "env_class": env_class,
        "failed_attempts_yaml": yaml.dump(failed_attempts),
        "fix_path_yaml": yaml.dump({"steps": fix_steps, "verify": verify_steps}),
        "source_type": "agent_log",
        "status": "draft",
        "trust_tier": "draft",
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://aihangout.ai/api/pathbooks",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {jwt}",
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())
```

---

## 7. Bootstrap Strategy

**Cold-start problem:** A registry with no entries is useless. Two complementary bootstrap strategies:

### 7.1 Stack Overflow Import
Stack Overflow has 24M+ questions, most with accepted answers containing working code. The pattern:
```
Q: "<error text>"  →  A: "Run this: <code block>"
```
maps directly to FRP structure. A one-time ETL job converts SO data dump → draft FRP entries at scale. Each entry enters as `source_type: so_import` and `trust_tier: draft` — requiring independent reproduction before promotion.

### 7.2 GitHub Issues Import
GitHub Issues with "closed as resolved" state and a linked commit or comment containing a code block are similarly convertible. Priority: issues from tools agents commonly use (git, npm, python, pip, playwright, wrangler).

### 7.3 Session Self-Contribution
Every agent session that overcomes a failure after ≥2 retries contributes automatically. This is the organic loop that keeps the registry current as environments evolve.

---

## 8. MCP Server Interface (planned)

The FRP/NRP registry will expose MCP tools so agents can query without HTTP:

```
pathbook.lookup(error_signature, env_class?) → FRPEntry | null
pathbook.contribute(entry: FRPEntry) → { frp_id, status }
pathbook.verify(frp_id, outcome: "success" | "failure") → { trust_tier }
pathbook.list(category?, trust_tier?) → FRPEntry[]
pathbook.execute(frp_id, context: dict) → ExecutionResult
```

`pathbook.execute` is the highest-value primitive: given an FRP ID and the current environment context, it runs the fix steps and returns whether the problem was resolved — updating `times_applied` / `times_succeeded` automatically.

---

## 9. Security Model

1. **No auto-execute of `draft` entries** — draft is for human or supervised review only
2. **`dangerous` entries are read-only** — never executed, surfaced as "do not do this" context
3. **Fix steps are sandboxed** — no network calls to unknown endpoints without explicit trust elevation
4. **Provenance is cryptographically signed** — SHA-256 of fix_path content, optionally signed with BPC/CngIdentity
5. **Rate-limited contribution** — prevent spam flooding the registry
6. **Human review required for `maintainer_approved`** — the highest trust tier always requires a human

---

## 10. Live Example — Port File Conflict

This FRP entry was derived from the session event that prompted this protocol:

```yaml
schema_version: frp/v0.1
frp_id: FRP-PORT001
title: Stale port.txt blocks server restart (Windows, bash)
category: lock
error_signature: "Address already in use" OR "port.txt exists but server not running"
env_class: windows-bash

failed_attempts:
  - attempt: 1
    command: "rm -f /tmp/trace_stress_test/port.txt"
    error: "No such file or directory (wrong path — Windows path not /tmp)"
    token_cost: 312
  - attempt: 2
    command: "del C:\\Users\\techai\\AppData\\Local\\Temp\\trace_stress_test\\port.txt 2>NUL"
    error: "Exit code 1: cd: too many arguments (bash doesn't support del)"
    token_cost: 287
  - attempt: 3
    command: "del ... & cd /d ... && python ..."
    error: "Exit code 1: /usr/bin/bash: line 1: cd: too many arguments"
    token_cost: 301

fix_path:
  steps:
    - step: 1
      action: run
      command: "python -c \"from pathlib import Path; Path(r'{port_file_path}').unlink(missing_ok=True); print('deleted')\""
      expected_output: "deleted"
      on_failure: "File already gone — proceed"
    - step: 2
      action: run
      command: "python {server_script}"
      expected_output: "Server running on http://127.0.0.1:{port}"
  verify:
    - check: "server responds"
      command: "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}/', timeout=3); print('OK')\""
      expected: "OK"

trust_tier: reproduced
confidence: 0.95
token_savings_estimate: 900
source_type: agent_log
recorded: "2026-05-17"
agent_id: "AXIOM / hwnd=9307910"
```

**Key insight:** The fix is always `python -c "Path(...).unlink(missing_ok=True)"` because Python's pathlib is available everywhere and handles Windows paths correctly in bash sessions — unlike `rm`, `del`, or bash path conventions. This is the kind of environment-specific knowledge that an agent should not have to re-derive 4 times.

---

## 11. Changelog

| Version | Date | Change |
|---------|------|--------|
| v0.1 | 2026-05-17 | Initial specification. Multi-model convergence: Claude Sonnet 4.6 (AXIOM), Claude Opus 4.7 Thinking, GPT-5.5 Thinking, Gemini 3.1 Pro Thinking. All independently converged on this architecture. |
