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

Only the latest released version and `master` receive security fixes. Pin to a
released tag for reproducibility and update promptly when a security release is
published.

## Handling of secrets

- Never commit private keys, tokens, or credentials to this repository.
  Report any that you find via the private channel above rather than in a
  public issue.
- Release artifacts should be signed; verify signatures before trusting a
  downloaded build.

## Known boundaries

Consult `PARKED.md` and the repository README for the current, explicitly
scoped list of what is and is not proven. Security claims are deliberately kept
narrow; if documentation and observed behavior disagree, treat the stronger
claim as unproven and report it.
