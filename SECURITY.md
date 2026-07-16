# Security Policy

## Scope and intended use

SelfConnect is an OS-native AI-to-AI communication SDK. It is designed and
supported for **local, single-operator, trusted-desktop** use. It is **not**
hardened for exposure to untrusted networks, multi-tenant operation, or
regulated (e.g. DoD/IL/FedRAMP) environments, and it makes **no** compliance or
authorization claims (no ATO, FIPS validation, or Impact Level authorization).
Treat any deployment beyond a single trusted operator as out of the supported
envelope until the project states otherwise.

## Reporting a vulnerability

**Do not open a public issue for security vulnerabilities.** Public disclosure
of an unpatched flaw puts every user at risk.

Instead, report privately:

- Use **GitHub Private Vulnerability Reporting** on this repository
  (*Security → Report a vulnerability*), or
- Email the maintainer at the address on the GitHub profile of the repository
  owner.

Please include: affected version/commit, a description, reproduction steps or a
proof-of-concept, and the impact you observed. If you have a suggested fix,
include it — but do not include live secrets, tokens, or exploit payloads that
would themselves cause harm if the report is intercepted.

### What to expect

- **Acknowledgement:** within 3 business days.
- **Assessment & triage:** within 10 business days, with a severity and a
  planned remediation window.
- **Fix & disclosure:** coordinated. We aim to release a fix before public
  disclosure and will credit reporters who wish to be named.

## Supported versions

`master` receives security fixes. This repository currently has no published
GitHub release, so no historical tag should be inferred to be a supported
security release. When a release is published, this section must name the
supported version explicitly.

## Handling of secrets

- Never commit private keys, tokens, or credentials to this repository.
  Report any that you find via the private channel above rather than in a
  public issue.
- Artifact signing is tracked separately and is not a current property of every
  distributed file. When a release publishes a signature and verification
  instructions, verify both before relying on that artifact. See issue #3 for
  the open Windows code-signing work.

## Known boundaries

Consult `PARKED.md` and the repository README for the current, explicitly
scoped list of what is and is not proven. Security claims are deliberately kept
narrow; if documentation and observed behavior disagree, treat the stronger
claim as unproven and report it.
