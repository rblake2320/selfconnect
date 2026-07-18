# Windows Terminal Active-Tab Guard

Status: bounded mitigation, live-tested on Windows Terminal; not exclusive
per-tab input routing.

`TerminalTabGuard` captures the selected Windows Terminal `TabItem`, retains
its live UI Automation element, and binds the following immutable identity to
the guarded-submit operation and acknowledgement:

- guarded top-level HWND, PID, and process start time;
- retained `TabItem` plus its opaque UIA RuntimeId;
- selected state;
- focused text-capable TermControl RuntimeId;
- peer `birth_id`.

The guard calls `SelectionItem.Select`, then verifies the retained element with
`IUIAutomation::CompareElements`. It checks immediately before each native
input batch and immediately after the batch. Drift before a native call is a
refusal. Drift after a native call is ambiguous because input may already have
taken effect.

## Live Evidence

The controlled drill creates one owned Windows Terminal window with two tabs
that have the same visible title. It proves:

- duplicate titles are not identity;
- a real command-palette tab reorder preserves the retained element identity;
- selecting the wrong tab is denied and explicit reselection restores it;
- moving focus between split panes changes TermControl identity and is denied;
- a tab change after one `PostMessageW` call is reported as ambiguous;
- a closed retained tab is denied;
- a replacement tab with the same title does not satisfy the stale identity.

Artifacts:

- `sc_terminal_tab.py`
- `tests/test_terminal_tab_guard.py`
- `experiments/win32_probe/terminal_active_tab_guard_probe.py`
- `experiments/win32_probe/results/terminal_active_tab_guard_LIVE_PASS_redacted.json`

## Assurance Boundary

UIA RuntimeIds are opaque, session-scoped comparison material and can be
reused. They are not durable or global identifiers. Title and tab index are
never identity. The retained COM element and RuntimeId checks reduce stale-tab
and wrong-active-tab risk during a bounded operation; they cannot make UIA
selection and later `PostMessageW`/`SendInput` atomic.

Windows Terminal exposes one shared top-level input path for its tabs. This
control therefore does **not** claim exclusive hardware or message routing to a
particular tab. A birth-ID-bound named-pipe/control-plane input channel inside
the receiving process remains the preventive design for exact per-peer routing.

Microsoft references:

- UIA RuntimeIds: https://learn.microsoft.com/windows/win32/winauto/uiauto-runtime-ids
- UIA AutomationId limits: https://learn.microsoft.com/dotnet/framework/ui-automation/use-the-automationid-property
- SelectionItem.Select: https://learn.microsoft.com/dotnet/api/system.windows.automation.selectionitempattern.select
- Windows Terminal actions: https://learn.microsoft.com/windows/terminal/customize-settings/actions

