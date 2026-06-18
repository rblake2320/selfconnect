# Local Model SelfConnect Proof

Last updated: 2026-06-18

## Result

PASS: a local Ollama model generated a packet, and SelfConnect delivered that
packet through the Win32 terminal transport into a throwaway receiver process.

Artifact:

```text
experiments/win32_probe/results/local_model_selfconnect_pass_BB728109.json
```

Probe:

```powershell
python experiments\win32_probe\local_model_selfconnect_probe.py --model hermes3:3b
```

## Hardware Safety

The tested machine had an RTX 5090 with about 32 GB VRAM. The selected model was
`hermes3:3b`, already installed through Ollama and small enough for a safe smoke
test. During the successful run, VRAM stayed around 2.7 GB used with about
29.4 GB free.

Do not use `llama4:scout` for this smoke path; it is far larger and unnecessary
for validating SelfConnect transport.

## Control Path

```text
Ollama local generate
  -> SelfConnect Win32 send_text_to_window
  -> throwaway terminal stdin
  -> receiver log verification
```

No cloud model, MCP, CDP, browser extension, WebDriver, or API-key control path
was required for this local-model transport proof. Ollama's local HTTP endpoint
was used only to run the local model on the machine.

## Honest Scope

This proves a local model can participate in the SelfConnect process through a
local wrapper/harness. It does not yet prove a fully autonomous local model with
its own tool-use loop, registry heartbeat, or long-running mesh role. Those are
the next layers to build.
