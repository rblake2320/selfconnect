# SelfConnect MCP Bridge Threat Model

Status: M4 read-only bridge gate, feature-flagged and default-off.

## Trust boundaries

The local model is untrusted for authorization, schema approval, target
selection, and completion. MCP server descriptions, schemas, tool results, and
errors are external untrusted data. The SelfConnect runtime, capability broker,
DPAPI-protected integrity key, digest-pinned server configuration, and
operator-approved schema fingerprints form the trusted boundary.

An MCP server process runs as the current Windows user. Approval does not make
the server process or its result text trustworthy; it approves only an exact
server-config digest plus tool name/description/input-schema fingerprint for
exposure through a specific broker permission.

## Admitted scope

- stdio transport only;
- short-lived sessions;
- explicit command, arguments, working directory, environment-variable names,
  timeout, and per-tool permission map in a digest-pinned config;
- read-only proof server and read permission for M4;
- at most 100 tools per server;
- bounded JSON-schema depth and property count;
- no `$ref`, composition, negation, or undeclared arguments;
- bounded result serialization;
- results marked `untrusted_data=true`;
- capability execution rebound to the inspected manifest digest.

Network transports, implicit permission defaults, model-created approvals,
write/mutation tools, arbitrary environment inheritance, and automatic
execution of first-seen or changed schemas are outside M4.

## Required controls

1. First-seen tools are quarantined and absent from discovery.
2. Approval is bound to the exact server-config digest and tool fingerprint.
3. Approval rows are HMAC-authenticated using the DPAPI-protected capability
   integrity key; a local file rewrite re-quarantines the tool.
4. A schema or description change invalidates approval.
5. Instruction-like external descriptions are rejected after approval and
   remain undiscoverable.
6. Every admitted tool has an explicit permission mapping. Missing mappings
   quarantine the tool.
7. Broker policy runs before the MCP adapter. Denial produces evidence without
   calling the server tool.
8. MCP results remain untrusted data and never enter system-prompt position.
9. Runtime launch must explicitly enable the Capability Kernel and dynamic
   skills, provide a digest-pinned config path, and grant the named MCP
   permission.
10. Every MCP quarantine, approval, denial, and completed call is written to
    authenticated capability evidence.

## Failure handling

Startup fails loudly on malformed or unpinned server configuration. Tool-list
and call deadlines are bounded. Incomplete calls cannot satisfy capability
completion because the broker verifier requires a successful adapter result.
Task recovery uses the current session authority and the step-bound manifest
digest.

The Python MCP SDK owns stdio subprocess cleanup. A hostile same-user server
that defeats SDK cancellation is not considered contained by M4; future
mutation-capable MCP admission requires Windows Job Object containment and a
live forced-timeout process-tree proof.

## Verification gate

M4 is complete only when:

- real stdio integration tests pass without fake clients;
- a real hostile-description server tool remains quarantined;
- a rewritten approval fails authentication;
- an unprivileged broker call is denied before server execution;
- a fresh Qwen instance uses only the three capability meta-tools to discover,
  inspect, and execute the approved read-only MCP capability;
- the returned repository identity matches real repository state and evidence
  chain verification passes.
