# Browser Local Proof

Last updated: 2026-06-17

This proof tests browser capability without touching real sites or attempting
CAPTCHA bypass.

## Purpose

The earlier SelfConnect proofs focused on terminals and simple desktop apps.
This proof adds a browser-specific lane:

- open Edge or Chrome as a normal Windows desktop app;
- verify the browser HWND/PID/exe/class/title before interaction;
- read owned page state through UIA/structured readback;
- fill a normal local form;
- detect a protected human-verification checkpoint and stop.

It is intentionally local and permissioned. The page is served from
`127.0.0.1` by `experiments/win32_probe/browser_local_proof.py`.

## What It Proves

If the probe returns `PASS`, it proves:

- SelfConnect can launch an isolated browser profile.
- SelfConnect can find the intended browser window by unique title nonce.
- SelfConnect can target-guard a non-terminal browser HWND.
- SelfConnect can read local page text through UIA/structured readback.
- SelfConnect can fill a normal owned form through UIA or foreground
  clipboard/keyboard fallback.
- SelfConnect can observe a submitted result sentinel.
- SelfConnect can detect a mock protected checkpoint and pause rather than
  bypass it.

## Current Live Result

On 2026-06-17, this probe returned `PASS` against Microsoft Edge:

- `read_method`: `uia_text`
- `input_method`: `uia_value_or_keys`
- `form_submitted`: `true`
- `protected_detected`: `true`
- `route_isolated`: `true`
- `capture_ok`: `true`

The redacted artifact is:

`experiments/win32_probe/results/browser_local_proof_PASS_redacted.json`

## What It Does Not Prove

This proof does not claim:

- real-site automation;
- CAPTCHA bypass;
- anti-bot evasion;
- WebAuthn/passkey automation;
- cross-browser parity;
- multi-tab routing inside a single shared browser profile;
- reliable background `WM_CHAR` delivery into Chromium page content.

Chromium browser content is not the same class of target as a ConPTY terminal.
`WM_CHAR` is strong for terminal peers; browser page control normally needs UIA,
foreground keyboard input, WebView2/DOM integration, or a browser-specific
adapter. This proof is deliberately honest about that boundary.

## Protected-State Boundary

For CAPTCHA-like surfaces, the desired SelfConnect behavior is not bypass.
The desired behavior is:

1. detect that a protected/human-verification state exists;
2. stop automated action;
3. request human completion or use an official test/sandbox flow;
4. record the pause as a governed boundary.

That supports enterprise/government posture because it proves SelfConnect can
recognize a trust boundary and refuse to pretend it is a human.

## Running

```powershell
python experiments\win32_probe\browser_local_proof.py --verbose
```

To write a redacted PASS artifact:

```powershell
python experiments\win32_probe\browser_local_proof.py `
  --output experiments\win32_probe\results\browser_local_proof_PASS_redacted.json `
  --verbose
```

Set a browser explicitly when needed:

```powershell
$env:SELFCONNECT_BROWSER_PATH = "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
python experiments\win32_probe\browser_local_proof.py --verbose
```

## Result Shape

The redacted artifact keeps:

- verdict;
- browser kind/exe;
- nonce and payload hashes;
- readback method;
- input method;
- route isolation result;
- protected-state detection result;
- latency;
- title hashes.

It redacts:

- HWNDs;
- PIDs;
- raw page text;
- local profile paths;
- screenshots.

## Next Work

Useful follow-up proofs:

- single-profile multi-tab routing using title nonce and active tab state;
- browser download/upload flow on a local test page;
- WebView2-hosted local app proof;
- governed-mode policy wrapper for browser actions;
- explicit CAPTCHA boundary event in the audit ledger.
