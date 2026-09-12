# SETUP — D5 Source-Control Integration (GitHub App)

This service (`services/source-control/`) implements the real GitHub App
authentication flow, the real GitHub REST API v3 request shapes, and real
local `git worktree` mechanics — all exercised in this repo's test suite
against a local mock GitHub server (`tests/mock_github_server.py`) and a
real local git fixture repository. **No live GitHub organization, App
registration, or repository exists in this environment.** This document
is the precise runbook for a human with real GitHub org-admin access to
finish standing this up against an actual organization. Nothing below can
be done by an agent in this environment — it all requires a real GitHub
account with admin rights on the target organization.

## 1. Register the GitHub App

In the target GitHub organization: **Settings → Developer settings →
GitHub Apps → New GitHub App**.

- **GitHub App name**: choose the slug carefully — it becomes this App's
  permanent bot identity in every audit log and PR, as `<slug>[bot]`
  (e.g. `sdlc-auto[bot]`). This is what makes the integration a
  distinguishable non-human identity per spec Section 15/17.1, so pick
  something that reads unambiguously as automation, not a person's name.
- **Homepage URL**: your organization's internal docs page for this
  System is fine.
- **Webhook**: enabled, pointed at this deployment's webhook endpoint
  (the job dispatcher / this service's webhook receiver — see spec
  Section 4.4/14.11 for the equivalent Jira-side mechanism this mirrors).
  Generate a **webhook secret** and store it the same way as the private
  key (step 3) — `source_control.webhook.verify_signature` is the
  verification code already implemented and tested against this exact
  HMAC-SHA256 scheme.
- **Permissions** (repository permissions only — grant nothing at the
  organization level; per-repository installation scope is itself part
  of the Section 22 "over-broad grant" mitigation):
  - **Contents**: Read & write (branch/worktree creation, file reads)
  - **Pull requests**: Read & write (open PR, poll status)
  - **Checks**: Read-only (poll CI check-run results)
  - **Metadata**: Read-only (mandatory baseline permission)
  - Do **not** grant Issues, Actions, Administration, or anything beyond
    the above — the smallest set this service's five tools actually use.
- **Webhook events** to subscribe to:
  - `installation` (required — this is how the service learns the App
    was uninstalled/suspended; `source_control.webhook.
    is_installation_revocation_event` already handles the `deleted` and
    `suspend` actions)
  - `installation_repositories` (repository added/removed from an
    existing installation)
  - `pull_request` (optional but recommended, for faster status updates
    than polling alone)
  - `check_run` / `check_suite` (optional, same reason)
- **Where can this App be installed?**: "Only on this account" — never
  "Any account." This App is one tenant's identity; it must not be
  installable by other organizations.

Click **Create GitHub App**. Note the **App ID** shown on the resulting
page — it is the `app_id` this service's `AppCredentials`/
`TenantInstallation` needs.

## 2. Generate and download the App's private key

On the App's settings page: **Private keys → Generate a private key**.
This downloads a `.pem` file once — GitHub does not retain a copy.

- Treat this key with the same handling as any tenant credential under
  spec Section 17.3: it must be wrapped under that tenant's own KMS key
  before it is ever stored, and decrypted only inside that tenant's
  compute cell at the point of use. **Do not** commit this file, put it
  in a `.env` file read by `os.environ`, or store it unwrapped in any
  shared secret store — `services/source-control/tests/
  test_no_personal_access_token.py::test_no_env_var_is_read_anywhere_for_credential_material`
  is a standing fitness test that the implementation never reads
  credential material from a process environment variable, precisely so
  this key is always threaded in from a real per-tenant secret-management
  integration, not a shortcut.
- Rotate this key periodically (GitHub supports multiple concurrent
  keys during rotation) and immediately if it is ever suspected exposed.

## 3. Install the App on the target repositories

Still on the App's page (or via the org's **Installed GitHub Apps**
settings): **Install App**, then choose **Only select repositories** and
pick exactly the repositories this tenant's deployment is meant to touch
— never "All repositories." This is the concrete Section 22 mitigation
("over-broad GitHub App grant" risk): the installation's repository list
*is* this tenant's access boundary, on top of (not a substitute for) this
service's own `InstallationRegistry.allowed_repositories` allow-list.

After installing, note the **Installation ID** from the URL
(`https://github.com/organizations/<org>/settings/installations/<id>`)
or via `GET /orgs/{org}/installations` — this is the `installation_id`
this service's `TenantInstallation` needs.

## 4. Wire the tenant's registration into this service

Construct one `source_control.service.TenantInstallation` per tenant
(this is deliberately not automated by this deliverable — a real
deployment's provisioning/onboarding flow owns this, reading the App ID,
installation ID, and unwrapped private key from that tenant's own
KMS-backed secret store at process start, per spec Section 17.3):

```python
from pathlib import Path
from source_control.service import InstallationRegistry, SourceControlService, TenantInstallation

installation = TenantInstallation(
    tenant_id="tenant-acme",
    installation_id="<installation id from step 3>",
    app_id="<App ID from step 1>",
    app_slug="<the App's slug, e.g. 'sdlc-auto'>",
    private_key_pem=b"<unwrapped PEM bytes, from this tenant's KMS-wrapped secret>",
    allowed_repositories=frozenset({"acme-org/service-a", "acme-org/service-b"}),
    mirror_root=Path("/var/sdlc-auto/tenant-acme/mirrors"),  # local clones live here
    # api_base_url defaults to https://api.github.com; override only for
    # GitHub Enterprise Server.
)
registry = InstallationRegistry()
registry.register(installation)

from source_control.mcp_server import build_server
server = build_server(registry)
server.run()  # speaks MCP over stdio, same as the F3 stub servers
```

`mirror_root/<owner>/<repo>` must exist as an already-cloned local mirror
of each allowed repository before `create-branch-worktree` is called
against it — see `git_ops.clone_or_update_mirror` /
`git_ops.build_authenticated_remote_url`, which build the
`https://x-access-token:<installation-token>@github.com/...` clone URL
from a freshly-issued installation token. Run an initial clone (or a
scheduled `clone_or_update_mirror` refresh) as part of this tenant's
onboarding.

## 5. Verify the installation end-to-end (real GitHub, by a human)

This is the one piece that genuinely cannot be exercised in this
environment and must be done once, by a human, against the real
organization:

1. Confirm `GET /repos/{owner}/{repo}` succeeds using a token minted via
   this service's own `GitHubAppClient.create_installation_token` (i.e.
   run this service for real, pointed at `https://api.github.com`, and
   call `create_branch_worktree`/`open_pr` against a real disposable test
   repository).
2. Confirm the resulting branch/PR/commit shows up in the repository's
   activity and the organization's audit log as `<app-slug>[bot]`, not as
   the admin's own account — this is the live confirmation of the bot
   identity requirement this service's tests already prove structurally.
3. **Exercise real revocation**: from the org's **Installed GitHub
   Apps** settings, uninstall the App from that one test repository (or
   suspend the whole installation), then confirm the next call this
   service makes against that repository fails with `permission-denied`
   (HTTP 401/403) — matching exactly what
   `tests/test_revocation.py` already proves against the mock. Then
   re-install and confirm access resumes.
4. Confirm the webhook delivery for that uninstall event arrives at this
   deployment's webhook endpoint, verifies (`webhook.verify_signature`)
   against the webhook secret from step 1, and is recognized by
   `webhook.is_installation_revocation_event`.

## 6. Ongoing operational notes

- **NHI inventory (spec Section 17.1)**: register this App installation
  by name (App slug + installation ID + owning tenant) in whatever
  system holds the organization's non-human-identity inventory, and
  reconcile it periodically against GitHub's own **Installed GitHub
  Apps** list — an installation nobody remembers granting must not
  persist unnoticed.
- **Key rotation**: generate a new private key (step 2) before revoking
  the old one; GitHub accepts either key until the old one is explicitly
  deleted from the App's settings.
- **Scaling to more repositories**: add them to the installation (step 3)
  and to this tenant's `allowed_repositories` allow-list together — never
  one without the other, since either alone leaves the two out of sync
  with what the other actually enforces.
- **GitHub Enterprise Server**: override `api_base_url` on
  `TenantInstallation` to the instance's API base; everything else in
  this document applies identically.

## What is real vs. mocked in this deliverable, for the avoidance of doubt

| Piece | Status |
|---|---|
| GitHub App JWT signing (RS256) | Real, tested against a real RSA keypair and a real signature-verifying counterpart |
| JWT → installation-token exchange | Real request/response shapes, tested against the mock server |
| Installation-token caching/expiry | Real, tested with real short expiries to force re-issuance |
| `git worktree add`/branch creation | Real, tested against a real local git repository |
| Concurrency safety of worktree creation | Real, tested with real racing OS threads/subprocesses |
| Open PR / get PR+check status / get file contents / list files | Real HTTP client code and request shapes, tested against the local mock server, not against api.github.com |
| Webhook signature verification | Real HMAC-SHA256, tested with real signatures |
| Revocation handling | Real code path (401/403 → permission-denied, not retried); the *trigger* (an actual GitHub App uninstall) is simulated at the mock server, since no live installation exists here |
| Actual App registration, webhook delivery from github.com, and a live installation | **Not done — requires the steps above, performed by a human with org-admin access** |
