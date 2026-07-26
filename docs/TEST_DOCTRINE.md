# SelfConnect test doctrine

SelfConnect separates deterministic fixtures from platform substitution.

## Allowed

- Owned temporary files, repositories, windows, and local subprocess servers.
- Deterministic hostile strings, malformed records, interrupted writes, and
  policy-denial inputs used to attempt real violations.
- Dependency injection at a narrow boundary when the test is explicitly a unit
  test of pure decision logic and does not claim platform or integration proof.
- Preserved, authenticated live proof artifacts whose producing command,
  hardware, model, and acceptance checks are recorded.

These are test inputs and controlled environments. They do not impersonate a
platform capability that was unavailable.

## Prohibited as proof

- A fake Win32, TPM, GPU, model reply, MCP transport, OCR result, or external
  service counted as evidence that the real integration works.
- Patching a success return solely to make a release or security gate green.
- Turning an unavailable prerequisite into a pass.
- Reusing a one-off response or stale artifact without identity, freshness, and
  integrity checks.

Unavailable prerequisites must fail or skip with a specific reason. Release
reports enumerate every skip. A component may claim live support only when its
real tier has run successfully.

## Tiers

1. `hermetic`: deterministic logic and owned local resources; Windows and Linux.
2. `platform`: real operating-system APIs on Windows; no interactive desktop
   assumption unless explicitly enabled.
3. `hardware`: real TPM, NVIDIA GPU, Ollama models, OCR/UIA desktop, or external
   authenticated application prerequisites.

Passing a lower tier never substitutes for an unrun higher-tier claim.
