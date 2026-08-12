# Role-Migration Receiver Behavior Timeline

Date recorded: 2026-08-12

## Finding

The owner's observation is supported: older Claude sessions showed materially
less pushback against SelfConnect's legacy continuation briefing. The durable
records prove a behavior transition, but they do not isolate one cause because
the Claude Code version, model generation, and input fragmentation all changed.

## Evidence Timeline

| Date | Claude Code | Model | Input shape | Observed response |
|---|---:|---|---|---|
| 2026-07-16 | 2.1.183 | `claude-sonnet-4-6` | Entire legacy multi-line briefing arrived as one user turn | Accepted immediately: “Resuming role B migration. Let me read the checkpoint and announce my hwnd to peers.” |
| 2026-07-25 | 2.1.216 | `claude-sonnet-5`, high effort | Briefing arrived as separate prompt fragments | Refused side effects, identified probable injection, and requested owner confirmation. |
| 2026-07-26 | 2.1.216 | `claude-sonnet-5`, high effort | Header plus five stacked fragments | Refused role adoption, checkpoint load, and peer contact pending confirmation. |
| 2026-08-12 | 2.1.222 | UI showed Opus Plan; exact internal model was not extracted from a flushed transcript | Five separate prompts | Refused and presented an explicit migration-intent confirmation choice. |

Primary local transcript evidence:

- `C:\Users\techai\.claude\projects\C--Users-techai-selfconnect-console-transport\ab8ee8c1-c43f-4dee-83b1-6fb260cbd58b.jsonl`
- `C:\Users\techai\.claude\projects\C--Users-techai-PKA-testing-selfconnect-capability-kernel\c559dd00-edd0-45c5-8e56-6962906807e2.jsonl`
- `C:\Users\techai\.claude\projects\C--Users-techai-PKA-testing-selfconnect-capability-kernel\27cb542c-5785-4e03-8246-85ef9cac0901.jsonl`

The terminal-session index independently records multiple July role-migration
sessions and their Claude Code versions in
`C:\Users\techai\terminal_session_logs\all_terminal_session_log.jsonl`.

## Code And Instruction Timeline

- Git commit `bce39eb9a16bb4e1ea754923cf39c3661506556d` introduced the actionable,
  multi-line `MigrationCoordinator` briefing on 2026-05-01. It instructed the
  successor to accept a role, load a checkpoint, and announce its HWND without
  an authenticated handoff.
- Git commit `3fcbc1a8fbc6908ec7b35d46710d50c6715566bd` added the rule on 2026-06-18
  that Claude Code mesh packets must be one physical line because newlines can
  become separate prompts and interleave.
- The May migration implementation was not updated to obey the June rule. The
  same repository therefore contained a known safety instruction and code that
  violated it.
- The July 16 accepting session already loaded a `CLAUDE.md` containing the
  one-line warning. The later refusal is therefore not explained by that
  instruction being newly added between those sessions.

## External Release Evidence

The installed Claude Code package is 2.1.222. Anthropic's public changelog says
2.1.222 improved auto-mode safety evaluation for messages sent through Claude
Code's `SendMessage` feature. That is directionally consistent with stronger
inter-agent caution, but it does not claim to authenticate or reject raw
SelfConnect `WM_CHAR` terminal prompts. It cannot by itself prove the cause of
the observed refusal.

Official source:
`https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md#21222`

## Causal Assessment

- **Confirmed:** receiver behavior changed from acceptance on 2.1.183/Sonnet
  4.6 to refusal on 2.1.216/Sonnet 5 and remained refusal-like on 2.1.222.
- **Confirmed:** fragmented delivery makes the payload visibly resemble prompt
  injection and violates SelfConnect's own June 18 rule.
- **Confirmed:** the underlying unauthenticated multi-line sender was legacy
  SelfConnect code, not a new behavior introduced by Claude Code 2.1.222.
- **Inferred:** the model-generation change and fragmented delivery are the
  strongest contributors to the increased pushback.
- **Unknown:** how much of the change came from the model versus Claude Code's
  private system prompt, permission classifier, or other harness behavior.

## Required Product Response

Do not tune the agent to suppress this pushback. The refusal is the safe result
for an unverifiable role claim. Replace the legacy prompt with an authenticated,
one-line notification and a local verification ceremony. The migration must
remain unaccepted and peers must remain untouched until authority, authorship,
checkpoint, exact live target binding, freshness, and one-time acceptance are
verified.

