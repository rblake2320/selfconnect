# SelfConnect Native Observation Proof

Date: 2026-08-24

## Outcome

SelfConnect now has one target-bound observation API for Windows apps,
browsers, and terminals while retaining its own Win32 identity, policy,
delivery-evidence, mesh, lease, and audit architecture.

The final live probe returned `PASS` for all thirteen checks. It used real
Windows processes and adapters; it did not use mocks, monkeypatches, simulated
adapter results, or prerecorded screenshots.

## Added surface

- CLI: `selfconnect resolve` and `selfconnect observe`
- MCP: `resolve_window` and `observe_window`
- Core composition: `sc_observation.py`
- Live tests: `tests/test_observation_live.py`
- Reproducible proof:
  `experiments/win32_probe/observation_state_live_probe.py`
- Redacted result:
  `experiments/win32_probe/results/observation_state_live_20260824_redacted.json`

## Core behavior

Unique resolution fails closed when a query has zero or multiple visible
matches. Observation binds all evidence to the same HWND and records identity
before and after reading. A changed, recycled, or closed target is rejected.

The evidence waterfall is adaptive:

1. Read through SelfConnect's existing UIA/child/console route.
2. Accept a rich accessibility result without paying screenshot/OCR cost.
3. If accessibility is empty or exposes only window chrome, capture pixels.
4. Reject low-information native frames.
5. Use screen pixels only when the exact target is foreground and not minimized.
6. Run optional Tesseract OCR and record method, quality, hashes, latency, and
   degraded reasons.

This waterfall is additive. It does not replace proven terminal delivery,
guarded input, or mesh identity.

## Real test surfaces

The probe launches and cleans up only owned targets:

- a real Tk Win32 application with high-contrast nonce text;
- two additional real app windows to prove ambiguity rejection;
- a real Microsoft Edge process with an isolated profile against an owned
  localhost page;
- a real `cmd.exe` console with unique target and content nonces;
- a genuinely terminated app HWND to prove stale/closed-target rejection.

All matching is performed through the production SelfConnect enumeration,
accessibility, capture, OCR, and identity paths.

## Final measured result

The redacted artifact records:

- status: `PASS`
- checks passed: 13 of 13
- Edge unique resolution: 2.487 ms
- Edge owned-page readiness: 560.458 ms
- terminal exact-resolution median: 1.544 ms
- terminal accessibility median: 34.037 ms over five real reads
- terminal forced-OCR median: 368.809 ms over three real reads
- measured adaptive advantage: 10.836x
- browser/app/terminal target stability: true
- ambiguity and closed-HWND rejection: true
- real app pixel quality accepted: true
- real OCR nonce evidence accepted: true

Measurements are machine- and fixture-specific. They are evidence for this run,
not a claim that every application or computer will achieve the same ratio.

## Failures that changed the design

The first app run returned a visually blank white frame. The native capture
path had previously treated only black frames as invalid. That real failure led
to luminance-variance quality checks and the guarded foreground capture
fallback.

Tk exposed its title bar through UIA but not its visible content. That failure
led to a chrome-only accessibility assessment and evidence escalation to OCR.

Terminal OCR recognized columnar console text out of reading order. The proof
was corrected to require a distinct content nonce plus expected words, rather
than pretending OCR preserves screen line order.

Edge exposed the correct HWND before the localhost page finished loading. The
browser lane now polls bounded readiness on the same identity-bound target
instead of treating window creation as page readiness.

## Reproduce

From the repository root:

```powershell
python experiments\win32_probe\observation_state_live_probe.py `
  --output experiments\win32_probe\results\observation_state_live_20260824_redacted.json `
  --verbose
```

Run the owned live pytest suite:

```powershell
python -m pytest tests\test_observation_live.py -q
```

## Provenance

No bundled Computer Use source, runtime, protocol, dependency, or private
implementation is included. The enhancement uses general concepts observed at
the capability boundary—unique selection, point-in-time state, adaptive
evidence, and stale-target rejection—implemented through SelfConnect's own
adapters and trust model.

This record supports engineering provenance and repeatability. It is not legal
advice or a patentability/freedom-to-operate opinion.

## Honest boundaries

This proof does not establish:

- public-site or every-browser compatibility;
- occluded GPU-window capture parity with Windows.Graphics.Capture;
- safe general UIA Value/Invoke mutation;
- stale-coordinate rejection for mouse actions;
- a universal confirmation taxonomy for consequential app actions;
- a 10x gain for every workload.

Those remain separate additions so they cannot silently weaken SelfConnect's
current identity, approval, and terminal-delivery guarantees.
