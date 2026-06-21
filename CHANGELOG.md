# Changelog

All notable changes to SelfConnect are documented here.

## [0.10.5] — 2026-06-21

### Added
- sc_fabric_windows_svc.py: Windows SCM service wrapper (pywin32 ServiceFramework)
  — SelfConnectFabricV2 service, auto-start, install/remove/start/stop/query
- sc_fabric_service.py: install-service, remove-service, start-service, stop-service,
  query-service CLI subcommands via sc_fabric_windows_svc
- sc_fabric_v2.py: create_pipe_security_attributes() — named-pipe DACL hardened to
  owner SID + SYSTEM, deny-all fallback; pipe_security_summary() for evidence
- runbooks/fabric_v2_windows_service.md: Windows service install/operate/remove runbook

### Changed
- Evidence matrix: Service SID daemon posture promoted from "documented/probed"
  to "productized"; DACL hardening promoted from "pending" to "proven hardened"
- Evidence matrix: Fabric V2 service wrapper boundary updated (SCM gap now closed)

### External blockers (no change, not engineering)
- TPM PASS: requires dTPM or Azure Attestation embodiment
- MSI signing: requires Azure Artifact Signing or SignPath Foundation
- Gemini scale: oauth-personal EOL June 18 2026; use Vertex AI API key
