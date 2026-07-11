# Runbook: Fix Gemini Not Working in Antigravity
<!-- selfconnect-runbook: {"status":"current","since":"2026-05-06","replacement":null,"entrypoint":false,"kind":"specialized"} -->

## What
Restore Gemini chat and code completion in Antigravity when it silently fails to load —
`fetchAvailableModels` succeeds but no chat panel activates.

## Prerequisites
- Antigravity installed (v1.107+)
- Google account signed in (personal Google One AI Pro/Ultra tier)
- Internet access to `daily-cloudcode-pa.googleapis.com`

## Diagnosis

### Step 1: Check the extension log
```
C:\Users\<user>\AppData\Roaming\Antigravity\logs\<latest session>\window1\exthost\google.antigravity\Antigravity.log
```
- **12 lines or fewer** = language server started but never received models → auth/config issue
- **227+ lines with "SupercompleteProvider"** = working session

### Step 2: Check renderer.log for agentSessions error
```
C:\Users\<user>\AppData\Roaming\Antigravity\logs\<latest session>\window1\renderer.log
```
- `[createInstance] ooe depends on UNKNOWN service agentSessions` = chat DI failure
  → Gemini UI loads and shows models, accepts messages, but NEVER calls streamGenerate
  → Fix: remove `worktreeConfig = true` from `.git/config` (see Fix 1 below)

### Step 3: Check for git errors in exthost.log
```
C:\Users\<user>\AppData\Roaming\Antigravity\logs\<latest session>\window1\exthost\exthost.log
```
- Dozens of `Error: Git error` at startup = unregistered nested git repos in workspace
- Fix: register all sub-directories as git submodules so git scanner doesn't choke on them

---

## Fixes (try in order)

### Fix 1: Remove worktreeConfig from .git/config (VERIFIED — session 15)
This fixes the `agentSessions UNKNOWN service` DI error that causes silent message dropping.

1. Open `.git/config` in the Antigravity workspace root
2. Remove the entire `[extensions]` block:
   ```
   [extensions]
       worktreeConfig = true
   ```
3. Kill ALL Antigravity processes (task manager, not just close window)
4. Restart Antigravity

Verification: send a test message in the chat panel — Gemini should respond within 2-3s.
Note: removing `worktreeConfig` disables per-worktree git config overrides. Claude Code
worktrees still function; only their worktree-specific config files are ignored.

### Fix 2: Re-authenticate
1. Open Antigravity
2. `Ctrl+Shift+P` → type **"Gemini: Sign In"**
3. Complete the browser OAuth flow
4. Restart Antigravity

### Fix 3: Sign out and back in
1. `Ctrl+Shift+P` → **"Gemini: Sign Out"**
2. Restart Antigravity completely (kill all processes)
3. `Ctrl+Shift+P` → **"Gemini: Sign In"**

### Fix 4: Switch to lower-tier model (quota exhausted)
- Click the model selector in the bottom-right of Antigravity
- Switch from **Gemini 3.1 Pro High** → **Gemini 3.1 Pro (Low)** or **Gemini 3 Flash**
- Exhausting quota on one model can lock out response generation

### Fix 5: Wait out rate limiting
- HTTP 429 rate limits are separate from daily quota — the quota meter shows fine but per-minute limits are hit
- Wait 5-15 minutes, retry during off-peak hours

---

## Known Failures

- **Setting `cloudcode.cloudProject` to a `gen-lang-client-*` ID**: Triggers the enterprise
  license path, causes "SUBSCRIPTION_REQUIRED" error. The personal Google One tier does NOT
  use a Cloud Project. Leave `cloudcode.cloudProject` unset for personal accounts.
  The `gen-lang-client-*` projects visible in Google Cloud Console are auto-created by AI Studio
  and are NOT Gemini Code Assist enterprise projects.

- **Git errors flood exthost.log at startup**: If you have unregistered nested git repos
  in your workspace folder (dirs with their own `.git` but not declared as submodules),
  the git extension throws 30+ errors per startup. This delays extension activation and
  can prevent Gemini from initializing. Fix: `git submodule add <url> <dir>` for each nested repo.

- **Model completely broken (504/503)**: Occasional Google infrastructure outages affecting
  both AI Pro and Ultra accounts. Check `discuss.ai.google.dev/c/antigravity` for status.
  Nothing to do but wait.

- **"Failed to set Cloud Code URL on Language Server"**: Appears in `cloudcode.log` on every
  startup — this is a harmless race condition (language server initializes 50ms after Cloud Code
  tries to set the URL). Not the cause of Gemini failing.

## Verified
- 2026-05-06, session 15 — Fix 1 (remove worktreeConfig) confirmed working. Gemini responded
  "WORKING" within 2s after clean restart. Antigravity v1.23.2, PKA testing workspace.
  Diagnosis path: renderer.log agentSessions error → .git/config worktreeConfig = true → remove → restart → live.
