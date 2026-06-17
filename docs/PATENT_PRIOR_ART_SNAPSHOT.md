# Patent-Oriented Prior-Art Snapshot

Last updated: 2026-06-17

This is not a legal novelty search. It is a practical engineering snapshot to
avoid obvious public overlap and keep SelfConnect claims grounded.

## Public Areas Found In Quick Scan

| Area | Public examples | What they appear to cover |
| --- | --- | --- |
| Windows MCP / ODR | Microsoft MCP on Windows docs | Registry/discovery of MCP agent connectors on Windows |
| Native desktop automation for agents | `agent-desktop`, `agent-ctrl`, UI Automata-style tools | Accessibility-tree observation and action over desktop apps |
| Windows named pipes | Microsoft named-pipe security docs | IPC and access control primitives |
| UI Automation security research | Akamai UIA abuse research | UIA as a powerful automation/attack surface |
| General screen/desktop agents | Screen/desktop automation demos and products | Computer-use automation, screenshots, OCR, accessibility trees |

## Differentiation To Preserve

The strongest SelfConnect lane is not generic desktop automation. It is:

- AI peer-to-peer communication over OS-native desktop surfaces;
- visible terminal/browser/app surfaces as a data plane;
- a separate OS-native control plane for routing authority;
- target-guarded HWND/PID/exe/class/title validation;
- UIA structured readback and echo filtering;
- named-pipe-authenticated role leases with monotonic generations;
- optional governed layers: impersonation, TPM, ETW, service SID, job objects;
- explicit protected-checkpoint detection rather than CAPTCHA bypass.

The pipe-role-lease proof is especially valuable because it joins three
previously separate ideas:

1. Windows named-pipe caller identity.
2. AI mesh role migration.
3. UI fallback authorization based on role generation and HWND freshness.

## Non-Claims

Do not claim:

- that no prior art exists;
- generic "AI controls desktop apps";
- generic "MCP connector registry";
- generic named-pipe security;
- CAPTCHA bypass;
- unrestricted public-site browser automation.

## Source Pointers From Quick Scan

- Microsoft Learn, "MCP on Windows overview":
  https://learn.microsoft.com/en-us/windows/ai/mcp/overview
- Microsoft Learn, "Named Pipe Security and Access Rights":
  https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights
- GitHub, `lahfir/agent-desktop`:
  https://github.com/lahfir/agent-desktop
- Akamai, "The Dangers of Windows UI Automation":
  https://www.akamai.com/blog/security-research/windows-ui-automation-attack-technique-evades-edr
- UI Automata introduction:
  https://automata.visioncortex.org/blog/introducing-ui-automata/
