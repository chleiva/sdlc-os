# D4 — Issue-Tracker Integration (Jira)

Implements F3's issue-tracker MCP contract
(`services/mcp-stubs/issue-tracker/schema/issue-tracker.schema.json`)
for real, against Jira Cloud REST API v3, per master spec Sec. 4.4.
Also owns the Atlassian Rovo MCP integration for Confluence research
(Sec. 15) and the Sec. 17.3 per-tenant HMAC webhook-signing scheme.

See `SETUP.md` for exactly what a human with real Jira org-admin access
still needs to do by hand, and for the one real spec ambiguity this
deliverable ran into (Jira Automation's native inability to compute an
HMAC signature at send-time).

## Layout

```
src/issue_tracker/
  config.py          per-tenant Jira configuration (trigger status, opt-in
                      label, gate statuses, allowed-transition allowlist)
  errors.py           F3's four named error conditions + HTTP -> error mapping
  result.py           the ok/empty/error wire envelope (matches F3's schema)
  adf.py              Atlassian Document Format helpers (Jira v3 requires ADF)
  jira_client.py       real Jira Cloud REST API v3 HTTP client
  gating.py            opt-in gating logic (the one choke point for dispatch)
  webhook_signing.py   Sec. 17.3 per-tenant HMAC sign/verify
  webhook_relay.py     the Automation-rule payload contract + signing relay
  rovo_client.py       Confluence research via the Rovo MCP pattern
  mcp_server.py        real MCP server: F3's schema, backed by JiraClient
  schema_loader.py     tiny JSON-Schema file loader

mocks/
  jira_mock_server.py  local HTTP mock of Jira Cloud REST API v3
  rovo_mock_server.py  local HTTP mock of the Rovo MCP JSON-RPC shape

tests/                 pytest suite exercising every module above against
                        the local mocks (see "Running the tests" below)

automation-rule.json   the Jira Automation rule's exact trigger/condition/
                        action config, as an import-assisted artifact
SETUP.md               the manual runbook for a human with real Jira access
```

## Running the tests

```sh
cd services/issue-tracker
python3 -m venv .venv
# kms-boundary first: this package's own pyproject.toml depends on it
# (TenantJiraConfig.from_wrapped_oauth_token's real Sec. 17.3 KMS-unwrap
# path) -- undocumented here until a Docker Compose packaging pass
# (D14) hit the resulting install failure and traced it back.
.venv/bin/pip install -e ../kms-boundary
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

No live Jira, Atlassian, or Rovo account is required or contacted --
every test runs against the local mock HTTP servers in `mocks/`. See
SETUP.md for the honest boundary between what is proven here and what
still needs a live Jira org.
