# SETUP — manual runbook for a human with real Jira org-admin access

Everything in `src/` and `mocks/` is real code, tested against local
mocks (see README.md). Nothing in this repository has ever contacted a
real Jira Cloud site, a real Atlassian Rovo endpoint, or a real OAuth
app registration -- that is a hard constraint of the environment this
deliverable was built in. This document is the precise, step-by-step
list of what a human with real access must still do, and exactly what
this codebase already gives them to work with at each step.

## 0. What's real vs. mocked, stated plainly

| Piece | Real | Mocked |
|---|---|---|
| Jira Cloud REST API v3 request/response shapes (create issue, transitions, comments, issue links, `/search/jql`) | Yes -- `jira_client.py` | The Jira server on the other end (`mocks/jira_mock_server.py`) |
| ADF (Atlassian Document Format) description/comment bodies | Yes -- `adf.py` | n/a |
| Opt-in gating logic | Yes, fully -- `gating.py` (no live dependency at all) | n/a |
| Fixed gate-transition allowlist | Yes, fully -- `config.py`/`jira_client.py` (no live dependency at all) | n/a |
| Sec. 17.3 HMAC webhook signing/verification | Yes, fully -- `webhook_signing.py` (no live dependency at all) | n/a |
| Confluence research via the Rovo MCP tool-call shape | Yes -- `rovo_client.py` (JSON-RPC 2.0 `tools/call` over HTTPS, OAuth Bearer) | The Rovo endpoint on the other end (`mocks/rovo_mock_server.py`), **and** the exact tool names/argument shapes -- see step 5 |
| The Jira Automation rule itself | A best-effort, import-assisted JSON artifact (`automation-rule.json`) | Cannot be created by clicking through Jira's UI in this environment at all |
| OAuth app registration (Jira Cloud app / Atlassian Rovo) | n/a | Cannot be registered in this environment at all |

## 1. Register a real Jira Cloud app identity (Sec. 15/17.1)

This System must appear as its own non-human identity, not a personal
account:

1. In your Atlassian org, go to **developer.atlassian.com > Console >
   Create app** (OAuth 2.0 (3LO) app) scoped to Jira.
2. Grant it exactly the scopes `jira_write`, `jira_read` (add
   `jira_project_admin` only if your org's opt-in label needs to be
   created via API rather than by hand -- see step 3).
3. Complete the 3-legged OAuth authorization once (as a service
   account, not a personal user) to obtain a refresh token; wire your
   token-refresh flow to keep `TenantJiraConfig.oauth_bearer_token`
   populated with a live access token per tenant. This codebase does
   not implement OAuth token refresh itself (out of D4's scope --
   Sec. 17.1's broader non-human-identity lifecycle is a platform-wide
   concern) -- it only *consumes* a valid bearer token per call.
4. Note your Jira **cloud id** (Settings > System > you'll see it in
   the site URL redirect, or via `GET
   https://your-site.atlassian.net/_edge/tenant_info`). Set
   `TenantJiraConfig.base_url = f"https://api.atlassian.com/ex/jira/{cloud_id}"`.
5. Per Sec. 17.3, wrap this tenant's OAuth credential under a KMS key
   scoped to that tenant alone, decrypted only inside that tenant's own
   compute cell -- this codebase's `TenantJiraConfig` accepts a plain
   token/secret because *how* it is fetched/decrypted is a Sec. 14.6
   secrets-management concern outside D4's own scope; do not hardcode a
   plaintext token in configuration checked into any repository.

(Basic-auth mode -- `auth_mode="basic"` with an email + API token -- is
also supported by `jira_client.py` if your org prefers a classic API
token over an OAuth app; Sec. 17.1 prefers the OAuth app model, so
treat basic auth as a fallback only.)

## 2. Confirm/adopt the trigger status (Sec. 4.4)

Do **not** invent a new status. In each project this System will
serve:

1. Open the project's workflow (Project settings > Workflow).
2. Confirm it already has a status meaning "queued and ready to
   start" -- Jira's own default is **Selected for Development**. If
   your team's board uses a different name for that same meaning
   (e.g. "Ready", "To Do (Ready)"), use that name instead.
3. If no such status exists at all, add one to the existing workflow
   (a simple no-transition-logic addition) rather than asking this
   System to recognize a nonstandard convention.
4. Set `TenantJiraConfig.trigger_status` to the exact status name
   (case-sensitive, must match Jira's own status name exactly).

## 3. Register the opt-in label (or dedicated issue type)

1. Simplest path: labels need no admin setup in Jira Cloud -- just
   start applying the label (default `ai-factory`) to stories you want
   the System to pick up. Consider adding it to your project's "common
   labels" via Project settings > so it autocompletes for your team.
2. Alternative: create a dedicated issue type (Project settings >
   Issue types > Add issue type, e.g. "AI Factory Task") if your org
   prefers a type-level signal over a label. Set
   `TenantJiraConfig.opt_in_issue_type` accordingly (label and issue
   type opt-in are both supported; either alone is sufficient --
   `gating.py`'s `evaluate` treats them as an OR).
3. **Do not** rely on "in the trigger status" alone -- `gating.py` will
   refuse to opt a story in without one of the above, by construction,
   regardless of what any Automation rule condition also checks.

## 4. Create the trigger-status field mapping / gate statuses

1. Decide which existing status your workflow uses for the
   plan-approval gate (Sec. 4.4 suggests "In Progress" -- Jira's
   default) and which for the change-review gate (e.g. "In Review").
   These must already exist in the project's workflow, reachable via a
   transition from wherever the System leaves the issue at each stage.
2. Set `TenantJiraConfig.approval_status` and
   `TenantJiraConfig.change_review_status` to those exact names.
   `TenantJiraConfig.allowed_target_statuses()` derives the fixed
   allowlist `jira_client.py` enforces in code from exactly these two
   values -- there is nothing else to configure for the coarse-status
   guarantee.
3. If your project is a **classic (company-managed)** project rather
   than **team-managed**, Epic linkage uses a custom field, not the
   `parent` field: find your project's "Epic Link" custom field id
   (Jira admin > Issues > Custom fields, or `GET
   /rest/api/3/field` and look for `"Epic Link"`), and set
   `TenantJiraConfig.epic_link_mode = "customfield"` +
   `epic_link_custom_field_id = "customfield_XXXXX"`.
4. If you'd rather size stories via a real Story Points field instead
   of the default `size:S|M|L|XL` label, find that field's id the same
   way and set `size_field_mode = "customfield"` +
   `size_custom_field_id`.

## 5. Confluence / Rovo MCP -- confirm the real tool contract

`rovo_client.py` is built against the common `search`/`fetch`
remote-MCP tool-naming convention as its best-effort target shape --
**this is an assumption, flagged explicitly, not a confirmed fact**,
because this environment has no access to Atlassian's current Rovo MCP
developer documentation to verify the exact tool names/argument/result
schema. Before pointing this client at a real Rovo endpoint:

1. Register an Atlassian Rovo MCP OAuth client per Atlassian's current
   developer docs (search "Rovo MCP server" / "Atlassian Remote MCP
   Server" on developer.atlassian.com).
2. Confirm the actual tool names (may not be exactly `search`/`fetch`)
   and their argument/result JSON shapes.
3. Adjust `_TOOL_SEARCH`/`_TOOL_FETCH` and the argument-building /
   result-mapping code in `rovo_client.py`'s `search_confluence`/
   `get_page` to match -- the typed `Result` envelope this module
   returns to the rest of D4 is designed to stay stable across that
   adjustment; only the internal `_call_tool` argument shapes should
   need to change.
4. Point `RovoConfig.base_url` at the real endpoint and
   `oauth_bearer_token` at a real OAuth access token (same KMS-wrapped,
   per-tenant secret-handling note as step 1.5 applies here too).

## 6. Create the Jira Automation rule

`automation-rule.json` is a best-effort reconstruction of Atlassian's
own rule-export JSON shape (Atlassian does not publish this export
format as a stable, versioned schema, so import may or may not work
as-is against your Jira Cloud version):

1. **Try the import first**: Project settings > Automation > (···) >
   **Import rule**, upload `automation-rule.json`. If it imports
   cleanly, fill in the four placeholders it names in its own
   `_meta.placeholders_a_human_must_fill_in` field (project key,
   trigger status, opt-in label, tenant id, and the webhook relay's
   URL -- see step 7 below for what that URL is) via the rule's normal
   edit UI, then enable it.
2. **If import fails**, reconstruct it by hand using the same file as
   your spec, since its trigger/condition/action *values* are the
   authoritative part even if the JSON envelope doesn't import
   verbatim:
   - **New rule > Trigger: "Issue transitioned"** (any transition).
   - **Add condition > "Issue fields condition"**, or a raw
     **JQL condition**: `status = "<your trigger status>" AND labels =
     "<your opt-in label>"` (use "issue type" instead of "labels" if
     you opted for a dedicated issue type).
   - **Add action > "Send web request"**: URL = the webhook relay's
     endpoint (step 7); method `POST`; header `Content-Type:
     application/json`; body = custom data:
     `{"tenant_id": "<your tenant id>", "issue_key": "{{issue.key}}",
     "project_key": "{{issue.project.key}}"}`.
   - Enable the rule.
3. Repeat per project that opts into this System, or scope one rule
   across multiple projects via the rule's project-scope setting if
   your Jira plan supports multi-project automation rules.

## 7. Deploy the webhook relay, and wire D1's real URL

**Spec ambiguity flagged for a human to resolve** (see
`webhook_relay.py`'s module docstring for the full reasoning): Jira
Cloud's native "Automation > Send web request" action has no built-in
way to compute a Sec. 17.3 HMAC signature at send time -- it can only
attach static header values. Two ways to resolve this, in order of
preference:

- **Check first** whether your org's specific Jira Cloud plan/version
  offers a native signed-webhook feature for Automation's outgoing web
  requests (a shared secret Jira itself uses to compute a signature
  header, similar to how classic Jira system webhooks support a
  signing secret). If it does, and its signature scheme can be made to
  match Sec. 17.3's construction (HMAC-SHA256 over `timestamp + "." +
  body`), you can point the Automation rule directly at D1's real
  endpoint and skip the relay hop entirely -- adjust `webhook_signing.
  verify_request` on D1's side only if the header names/format differ
  from `X-SDLC-Tenant-Id`/`X-SDLC-Timestamp`/`X-SDLC-Signature`.
- **Otherwise (the safe default this codebase assumes)**: deploy
  `webhook_relay.serve_forever` (or wrap `RelayApp.handle_automation_event`
  in your production HTTP framework of choice) as a small, TLS-terminated
  internal service. Point the Automation rule's web-request URL at
  *this* relay, not at D1 directly. Configure the relay with:
  - `jira_client_for_tenant`: a real per-tenant `JiraClient` (step 1).
  - `secret_for_tenant`: your real per-tenant HMAC key store (KMS-backed,
    Sec. 17.3/14.6 -- never a single shared platform-wide secret).
  - `repository_for_project`: your real tenant+project -> repository
    mapping (however your org tracks which repo each Jira project
    corresponds to).
  - `dispatcher_url`: D1's real job-dispatcher endpoint, once D1 exists.
  Only once D1 exists should `RelayApp`'s constructed `SignedDispatch`
  actually be POSTed onward to it -- wire that HTTP call (using
  `dispatch.url`, `dispatch.headers`, `dispatch.body`) into your
  production relay deployment; this repo's own tests inject a
  `forward` callback instead of making that real outbound call, since
  D1 does not exist yet in this repository (see the honesty note in
  `tests/test_webhook_relay.py`'s module docstring).

## 8. Gate approver identity (Sec. 12.1)

No code change needed here, but worth confirming with whoever owns D9
(gates/governance): the plan-approval and change-review gates' human
identity resolves from the Jira issue's Assignee (or Reporter if
unassigned) at the moment the gate fires -- `get-issue`'s result
carries enough (`status`, and whatever assignee/reporter fields you
choose to add to `_ISSUE_KEY_FIELDS` in `jira_client.py` if D9 needs
them surfaced through this same MCP contract; the current field list
does not include assignee/reporter since F3's schema for `get-issue`
does not name them as required output fields -- flag this to D9 if it
turns out to need them from this server rather than resolving identity
some other way).

## 9. Smoke-test against the real Jira org before going live

Once steps 1-4 are done, before enabling the Automation rule for real
traffic:

1. Point a local `TenantJiraConfig` at the real `base_url` and a real
   token, run `jira_client.create_epic(...)` / `create_story(...)`
   once by hand (e.g. in a Python REPL) against a disposable test
   project, and confirm the created issues look right in Jira's UI,
   including the Epic/Story issue link direction.
2. Manually transition a test story through the trigger status with
   the opt-in label applied, and confirm the Automation rule fires
   (Automation's own audit log shows the rule run) and reaches the
   webhook relay.
3. Only then enable the rule on a real project with real traffic.
