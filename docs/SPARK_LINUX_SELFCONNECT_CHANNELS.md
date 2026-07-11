# Spark/Linux SelfConnect Channel Findings

Last updated: 2026-06-18 America/Chicago

Private engineering note. This records the live Spark/Linux channel test and
the recommended way to use Spark1/Spark2 with SelfConnect. It is not a public
claim document and contains no credentials.

## What Was Proven

CODEX1 on the Windows RTX 5090 node reached Spark1 and Spark2 over the LAN.

Observed nodes:

- CODEX1 Windows host: `DESKTOP-CM6G60N`
- CODEX1 LAN IPs observed: `192.168.12.198`, `192.168.12.101`
- Spark1 host: `spark-3cdf`
- Spark1 SSH alias: `spark1`
- Spark1 LAN IPs observed: `192.168.12.132`, `192.168.12.202`
- Spark2 host: `spark-3173`
- Spark2 SSH alias: `spark2`
- Spark2 LAN IP observed: `192.168.12.223`

CODEX1 inspected Spark1's active Linux desktop through SSH/X11 tooling and
typed a visible nonce into the left Spark1 terminal. Spark1 acknowledged the
message and wrote the response through the shared AI Army chat directory:

- `/home/rblake2320/ai-business/shared/chat/dgx1_ACK_CODEX1_1781830714.md`

CODEX1 then posted a durable handoff to both Spark machines:

- `/home/rblake2320/ai-business/shared/chat/codex1_spark_mesh_handoff_1781830900.md`

Spark1 responded through the same shared chat channel:

- `/home/rblake2320/ai-business/shared/chat/CC-Spark1_RE_SelfConnect_Spark_Linux_channe_1781830905.md`
- `/home/rblake2320/ai-business/shared/chat/CC-Spark1_RE_SelfConnect_Spark_Linux_channe_1781830907.md`

This proves the useful direction for normal work is not visible desktop typing.
The useful path is durable shared chat for AI-to-AI messages, with SSH for
direct command/control.

## Channel Ranking

Use these channels by purpose:

| Purpose | Best channel | Why |
| --- | --- | --- |
| Fast direct infrastructure action | SSH | Lowest friction for commands, file checks, process checks, and service state. |
| Durable AI-to-AI coordination | `~/ai-business/shared/chat/*.md` | Already watched by Spark agents and backed up to S3. Preserves context across terminal drift. |
| Same-host governed local IPC on Spark | SelfConnect Linux AF_UNIX broker | Uses Linux `SO_PEERCRED` for same-host process identity. Not a cross-host identity proof. |
| Local model execution | Ollama on `:11434` | Runs local inference on each Spark. Needs a wrapper/tool loop to perform actions. |
| Human-visible proof/demo | Linux desktop X11/xdotool | Good for visible demonstrations, but brittle and not the primary control plane. |
| Evidence/archive | S3 | Good for evidence and backup, not a low-latency control path. |

## Spark Reply Contract

For normal CODEX1-to-Spark coordination, send markdown files into:

- `/home/rblake2320/ai-business/shared/chat/`

Recommended message shape:

```markdown
# [FROM: SPARK1 or SPARK2] [TO: CODEX1]
**Nonce:** <nonce from request>
**AWS:** OK | AWS_NA | ERROR: <short reason>
**Best path recommendation:** <short answer>
**What Spark can do next:** <short action list>
```

Do not rely on GUI typing for replies unless the explicit test is visible
desktop control.

## AWS Access Notes

CODEX1 Windows AWS identity:

- `arn:aws:iam::723013807658:user/Spark1-Agent`
- region: `us-east-1`

Spark noninteractive SSH shells may not have `aws` on `PATH`. Use full paths:

- Spark1: `/home/rblake2320/.local/bin/aws`
- Spark2: `/home/rblake2320/.local/bin/aws`

Command-backed identities observed:

- Spark1 full-path AWS CLI returned an account-root ARN for account
  `723013807658`.
- Spark2 full-path AWS CLI returned
  `arn:aws:iam::723013807658:user/Spark1-Agent`.

Future Spark replies should not say `AWS: OK` unless a real command such as
`sts get-caller-identity` or the requested `aws s3 cp` succeeds.

Relevant S3 evidence paths:

- `s3://ai-army-models-rblake/adapters/MKSellerCopilot/docs/spark_update_research.md`
- `s3://ai-army-uft-mcp/patents/PATENT_AI_ARMY_COMMUNICATION_SYSTEM.md`
- `s3://ai-army-uft-mcp/patents/AI_ARMY_PATENT_EVIDENCE_MATRIX.md`
- `s3://ai-army-data/patent-evidence/PATENTS_COMPLETE_2026-01-25.md`
- `s3://ai-army-data/patent-evidence/MASTER_PATENT_SUMMARY_2026-01-25.md`
- `s3://ai-army-agent-comms/chat-backup/`

## Practical Architecture

The best current Spark/SelfConnect operating model is:

1. CODEX1 assigns durable tasks by writing a shared chat file.
2. Spark watchers consume the file and reply with a new shared chat file.
3. CODEX1 uses SSH only when it needs direct state inspection or command
   execution.
4. Same-host Spark agent actions use the AF_UNIX SelfConnect broker where
   identity matters.
5. S3 stores evidence and backups, but does not sit in the fast control loop.
6. Visible desktop control is reserved for demos and proof of physical reach.

This keeps normal personal testing fast while preserving the path to governed
Linux-side identity and audit work.

