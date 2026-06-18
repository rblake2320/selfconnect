# Channel Router Composition Proof

Last updated: 2026-06-17

This proof documents the policy composition that SelfConnect needs for patent
defense:

1. terminal surfaces use `WM_CHAR`;
2. browser surfaces use UIA Value/Invoke;
3. metadata and routing use a sidecar control plane, not visible terminal text;
4. governed mode requires a role lease;
5. readback is classified so local echo is not mistaken for peer output.

## Why This Matters

A competitor can avoid individual nouns:

- replace named pipes;
- replace `HWND`;
- replace `WM_CHAR`;
- replace UIA;
- wrap the system in MCP;
- use CDP or a browser extension.

This proof focuses on the invariant: one policy engine chooses the correct
native channel for the target surface and applies the same target/lease/readback
rules across terminal, browser, and metadata paths.

## Artifacts

- Probe:
  `experiments/win32_probe/channel_router_composition_probe.py`
- Redacted model artifact:
  `experiments/win32_probe/results/channel_router_composition_PASS_redacted.json`
- Redacted live artifact:
  `experiments/win32_probe/results/channel_router_composition_LIVE_PASS_redacted.json`
- Unit tests:
  `tests/test_channel_router_composition.py`

## Model Proof

The deterministic model proof does not touch live windows and does not use MCP.
It proves:

- terminal route -> `wm_char`;
- browser route -> `uia_value_invoke`;
- metadata route -> `file_registry`;
- governed routes require a matching lease;
- stale generation is denied;
- wrong target class/title is denied;
- metadata route has `no_visible_metadata=true`;
- echo-filtered readback returns a mixed echo/external classification.

Run:

```powershell
python experiments\win32_probe\channel_router_composition_probe.py `
  --output experiments\win32_probe\results\channel_router_composition_PASS_redacted.json `
  --verbose
```

## Live Mode

Live mode composes the existing throwaway terminal and local browser probes:

```powershell
python experiments\win32_probe\channel_router_composition_probe.py `
  --live `
  --output experiments\win32_probe\results\channel_router_composition_LIVE_PASS_redacted.json `
  --verbose
```

Live mode only uses throwaway/local targets:

- `uia_echo_filter_probe.py` spawns or uses a throwaway terminal target;
- `browser_local_proof.py` opens an isolated local browser profile against
  `127.0.0.1`;
- no public site, no CAPTCHA bypass, no MCP control path.

The committed live artifact is `PASS`:

- terminal: `TextChanged_event`;
- browser: local browser proof reported `input_method=uia_value_or_keys` and
  `read_method=uia_text`;
- router: terminal uses `wm_char`, browser route selects `uia_value_invoke`,
  metadata uses `file_registry`;
- stale generation and wrong target class fail closed.

If future live probes are unavailable, the proof returns `NA` rather than
overclaiming. A stricter browser-only rerun that proves UIA Value/Invoke with no
keyboard fallback remains tracked separately from this composition proof.

## Claim Boundary

This proof does not claim:

- universal browser automation;
- CAPTCHA bypass;
- first desktop automation bridge;
- production named-pipe daemon replacement;
- production TPM attestation.

It supports the narrower claim:

> A local AI peer mesh can route actions through target-specific native desktop
> channels while enforcing common target facts, lease freshness, and readback
> classification.

## Disclosure Warning

This file is private defensive evidence. Do not publish, quote in marketing, or
place into a public repository before patent counsel reviews it.
