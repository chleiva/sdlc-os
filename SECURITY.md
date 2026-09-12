# Security Policy

SDLC Auto runs AI coding agents against real source-control and issue-
tracker credentials (a GitHub App installation, a Jira OAuth token) and,
in its multi-tenant cloud deployment, executes model-generated code
inside per-tenant sandboxes. Treat any of the following as a security
issue, not a regular bug:

- A way to read, execute code in, or exfiltrate data from another
  tenant's cell, sandbox, or Registry rows (cross-tenant isolation
  failure).
- A way for the orchestrator's Bedrock/OpenAI/Anthropic/Ollama tool-use
  loop, or model-generated code, to escape its declared workspace scope
  or sandbox tier (see `services/orchestrator`'s `AgentBackend` and
  `SandboxTier` implementations).
- A credential-handling bug: a secret logged, persisted in plaintext
  where §17.3 requires KMS envelope encryption
  (`services/kms-boundary`), or a GitHub/Jira token usable outside its
  intended scope or tenant.
- A GitHub App or Jira integration bug that lets an action be attributed
  to the wrong actor, bypass an approval gate (`services/gates`), or
  merge/comment without the audit trail §17.1 requires.

## Reporting a vulnerability

**Do not open a public GitHub issue for a suspected vulnerability.**

Report privately via
[GitHub's private vulnerability reporting](https://github.com/chleiva/sdlc-os/security/advisories/new)
for this repository. Include:

- The affected service(s) under `services/` (or `infra/`), and the
  specific file/function if you know it.
- Steps to reproduce, or a minimal proof of concept.
- What you'd expect to happen instead, and the real-world impact (which
  isolation boundary or credential this crosses).

We'll acknowledge reports as soon as we can and work with you on a fix
and disclosure timeline before any public write-up.

## Scope notes

This project's current status (see `README.md` and `CLAUDE.md`'s "Known
cross-deliverable gaps") is: every component is real, independently-
tested code, but the system has not yet been run end-to-end against a
live cloud account or production tenant traffic. Reports about
*documented, already-disclosed* gaps (e.g. "the orchestrator has no
real process entrypoint yet", "the Run Registry has no network-
reachable server") are welcome as design discussion in a regular issue,
not as a private security report — they're already tracked, not hidden.
