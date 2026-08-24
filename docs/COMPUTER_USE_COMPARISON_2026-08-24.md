# SelfConnect vs. Bundled Computer Use

Date: 2026-08-24

## Evidence basis

This comparison uses the bundled Computer Use skill, guidance, API, and
confirmation-policy files shipped with Codex Desktop 26.818.31338, plus the
current SelfConnect 0.12.0 source, tests, runbooks, and live capability probes.

The live `@oai/sky` probe could not start because the Codex Windows sandbox
helper failed with Windows error 206 (path/filename too long). The bundled API
contract was readable and is the basis for the Computer Use side below.

## Summary

The systems overlap, but their centers of gravity are different:

- Computer Use is a broad, foreground-oriented Windows app operator. It exposes
  app discovery, exact window selection, point-in-time accessibility state,
  Windows.Graphics.Capture screenshots, and common mouse/keyboard actions.
- SelfConnect is a Windows-native agent and terminal bridge. It has stronger
  background terminal transport, delivery evidence, durable peer identity,
  orchestration, leases, and audit history.
- Before this change, SelfConnect's observation capabilities were fragmented
  across `read`, `capture`, delivery OCR, and the local-agent runtime.
- This change adds a unified, target-bound observation surface without replacing
  or weakening any existing transport.

## Capability comparison

| Area | Bundled Computer Use | SelfConnect |
| --- | --- | --- |
| Primary purpose | Operate Windows application UIs | Agent/terminal communication, orchestration, and guarded Win32 control |
| Window selection | Requires exactly one returned window | Legacy `find_target` can choose first match; new `resolve`/MCP `resolve_window` fails on zero or multiple matches |
| Observation | One state with window, accessibility, and screenshots | New `observe`/MCP `observe_window` composes identity, text, optional UIA elements, screenshot, OCR, hashes, and timing |
| Text reading | UIA tree, focused element, selected text/elements, document text | UIA text, child-window text, console fast path in core, optional bounded UIA element summaries |
| Screenshots | Windows.Graphics.Capture; designed to work when occluded | Existing native capture first; low-information frames can use a guarded foreground-only ImageGrab fallback that refuses occluded/minimized targets |
| OCR | Model can inspect captured pixels; no public Tesseract contract | Optional pytesseract adapter; automatic escalation when accessibility is empty or exposes only window chrome |
| Text input | Foreground SendInput-style literal typing | Exact-HWND WM_CHAR for tested Windows Terminal, console input records for legacy consoles, guarded SendInput fallback |
| Mouse/actions | Click, scroll, drag, set value, secondary accessibility actions | Absolute/window clicks, post-click, scroll, classic control messages, menus, combo/list selection |
| Terminals | Explicitly prohibited by Computer Use safety guidance | A core supported surface with delivery/readback semantics and agent workflows |
| Browser strategy | Prefer the separate Browser plugin | Native UIA/Win32 local-browser proof; public-site and cross-browser parity remain unproven |
| Target safety | Fresh state, screenshot IDs, element indexes, wrong-window point rejection | HWND/PID/exe/class/title guards, terminal-class default, optional lease/birth/generation binding |
| Side-effect policy | Detailed action-time confirmation taxonomy | Input gates, target guards, approval rules, governed profile, audit; no equivalent universal app-action taxonomy yet |
| Multi-agent coordination | Not part of the window API | Mesh registry, birth IDs, generations, hash-chained events, local-model roles, migration and fleet tooling |
| Performance posture | State capture is documented as expensive | Adaptive observation skips capture and OCR when UIA/child text succeeds |

## Enhancement added

### Unique resolution

`selfconnect resolve --query ...` and MCP `resolve_window` return success only
when exactly one visible window matches. Ambiguous results return bounded
candidates and perform no action.

This leaves the source-compatible `find_target` helper unchanged.

### Unified observation

`selfconnect observe --hwnd ...` and MCP `observe_window` return:

- window identity before and after observation;
- a fail-closed `target_stable` result;
- surface classification;
- UIA/child text and extraction method;
- optional bounded UIA element records with focus and bounds;
- optional screenshot path and image quality metadata;
- automatic or forced OCR results;
- SHA-256 hashes for text and screenshot pixels;
- a point-in-time observation ID;
- elapsed time and explicit degraded-state reasons.

Default `ocr_mode=auto` assesses whether accessibility exposed app content.
It skips capture/OCR for rich content, but escalates when UIA is empty or
contains only window chrome. Low-information native frames can use screen pixels
only when the exact HWND is foreground and not minimized; otherwise the result
stays explicitly degraded instead of capturing an occluder.

### Packaging and compatibility

The feature is in a separate `sc_observation.py` module and is additive to the
CLI and MCP adapters. Existing Win32 input, framing, mesh, approval, and read
contracts are unchanged. The new capture waterfall composes the existing native
capture and adds only a guarded foreground fallback.

## Live proof and measured speed

The reproducible probe
`experiments/win32_probe/observation_state_live_probe.py` launches owned real
Windows processes and uses actual HWND enumeration, UIA, pixels, Tesseract, and
process closure. It uses no mocks, monkeypatches, prerecorded screenshots, or
simulated adapter results.

The 2026-08-24 redacted PASS run proved all thirteen checks across a real
owned app, Microsoft Edge, and a real owned terminal, including ambiguity and
closed-HWND rejection. Edge exact resolution measured 2.487 ms and the owned
page became readable in 560.458 ms. On the terminal, repeated exact resolution
measured 1.544 ms median, the accessibility fast path measured 34.037 ms median
over five runs, and forced OCR measured 368.809 ms median over three runs: a
measured 10.836x fast-path advantage. These numbers describe this machine and
fixture, not a universal performance guarantee.

The first two live iterations were retained as development findings rather than
papered over: blank/low-information capture and terminal OCR token ordering both
caused real failures and directly changed the implementation and proof logic.

## Provenance and patent boundary

No Computer Use source code, runtime, protocol, dependency, or private
implementation was copied into SelfConnect. The work adopts general engineering
principles visible at the capability boundary: exact target selection, fresh
target-bound state, adaptive evidence, and stale-target rejection.

SelfConnect remains a composition of its own Win32/UIA/OCR adapters, guarded
identity, delivery evidence, mesh, leases, and audit model. This document is
engineering provenance, not a patentability or freedom-to-operate opinion; any
patent filing should still be reviewed by qualified counsel against prior art.

## Usage

Resolve a unique target:

```powershell
selfconnect resolve --query "Notepad" --exact-title
```

Fast text-first observation:

```powershell
selfconnect observe --hwnd 0x123456
```

Include a bounded UIA element summary:

```powershell
selfconnect observe --hwnd 0x123456 --elements --element-limit 200
```

Save a screenshot and force OCR:

```powershell
selfconnect observe --hwnd 0x123456 --screenshot --ocr-mode always
```

MCP callers use `resolve_window` followed by `observe_window`. Existing
`verify_target` remains mandatory before input through `send_text`.

## Remaining high-value gaps

These should remain separate, additive work rather than changes to proven
terminal transport:

1. Add an optional Windows.Graphics.Capture adapter so GPU-composited windows
   can be captured while occluded without relying on the visible desktop.
2. Add guarded UIA Value/Invoke actions that require a current observation and
   re-check target identity immediately before the action.
3. Bind coordinate actions to screenshot hashes/IDs so stale coordinates are
   rejected after layout changes.
4. Define a universal app-action confirmation taxonomy comparable to Computer
   Use before widening the default MCP mutation surface.
5. Keep browser DOM/CDP/WebDriver integration optional. It can improve speed on
   cooperative sites, but it should not replace SelfConnect's OS-native core.
6. Prove Chrome, Edge, and Firefox parity on owned fixtures, including OCR
   fallback and multi-tab stale-target detection.
