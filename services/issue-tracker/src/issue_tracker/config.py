"""Per-tenant configuration for the issue-tracker integration.

Every field here is deliberately configuration, not a hardcoded
platform constant (master spec Sec. 19): the trigger status, opt-in
label, and gate statuses are named by the *organization's own* existing
Jira workflow (Sec. 4.4), never invented by this System. `tenant_id` is
first-class per Sec. 14.13/14.12 and flows through to every Jira Cloud
REST API v3 call this client makes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kms_boundary import KmsBoundary, WrappedSecret


@dataclass(frozen=True)
class TenantJiraConfig:
    tenant_id: str

    # Jira Cloud site addressing + auth. Real Jira Cloud REST API v3 is
    # reached at either the classic per-site base URL
    # (https://<site>.atlassian.net) with Basic auth (email + API
    # token) or, for an OAuth 2.0 (3LO) app-installed integration, via
    # https://api.atlassian.com/ex/jira/<cloud_id>/ with a Bearer
    # access token. Sec. 17.1/17.3 favor the latter (a registered,
    # non-human app identity with short-lived tokens) but both are
    # valid v3 auth modes and the client supports either.
    base_url: str  # e.g. "https://api.atlassian.com/ex/jira/<cloud_id>" or "https://your-site.atlassian.net"
    auth_mode: str = "oauth_bearer"  # "oauth_bearer" | "basic"
    oauth_bearer_token: str | None = None
    basic_auth_email: str | None = None
    basic_auth_api_token: str | None = None

    project_key: str = "PROJ"

    # Opt-in gating (Sec. 4.4): a story is only picked up if it carries
    # this label OR this issue type -- never "in the trigger status"
    # alone. At least one of the two must be configured non-empty.
    opt_in_label: str | None = "ai-factory"
    opt_in_issue_type: str | None = None

    # Trigger status: "queued and ready to start" in the team's own
    # existing workflow -- default mirrors Jira's own out-of-the-box
    # "Selected for Development" status. Not invented by this System.
    trigger_status: str = "Selected for Development"

    # Gate transitions (Sec. 4.4, 12.1) -- the ONLY statuses this
    # client is ever allowed to request via transition-status. See
    # `allowed_target_statuses`.
    approval_status: str = "In Progress"
    change_review_status: str = "In Review"

    # Sizing field strategy: "label" (default; portable, needs no Jira
    # admin custom-field setup) writes/reads size as a `size:S|M|L|XL`
    # label. "customfield" reads/writes a real Jira custom field (story
    # points or a dedicated select field) whose id is org-specific.
    size_field_mode: str = "label"
    size_custom_field_id: str | None = None  # e.g. "customfield_10016"

    # Epic linkage: team-managed (next-gen) Jira projects use the
    # `parent` field directly; classic/company-managed projects require
    # the (org-specific) "Epic Link" custom field instead.
    epic_link_mode: str = "parent"  # "parent" | "customfield"
    epic_link_custom_field_id: str | None = None  # e.g. "customfield_10014"

    request_timeout_seconds: float = 10.0

    def allowed_target_statuses(self) -> frozenset[str]:
        """The fixed, small allowlist `transition-status` may ever
        request. This is Sec. 4.4's "Jira status stays coarse,
        deliberately" rule enforced in code (see `jira_client.py`),
        not left to caller discipline. Deliberately excludes
        `trigger_status`: this client never *requests* the trigger
        transition -- that transition is performed by a human/their
        own board workflow and is only ever the Automation rule's
        *trigger*, never our own action.
        """
        return frozenset({self.approval_status, self.change_review_status})

    def size_label(self, size: str) -> str:
        return f"size:{size}"

    @classmethod
    def from_wrapped_oauth_token(
        cls,
        *,
        wrapped_oauth_token: WrappedSecret,
        kms_boundary: KmsBoundary,
        **kwargs: object,
    ) -> "TenantJiraConfig":
        """Alternate constructor (additive -- the plain constructor
        above, which takes `oauth_bearer_token` as an already-plaintext
        string, is unchanged and still works exactly as before): builds
        a `TenantJiraConfig` by unwrapping `wrapped_oauth_token` via
        `kms_boundary.decrypt(tenant_id, ...)` at this exact point of
        use.

        This is the concrete implementation of what SETUP.md's step 1.5
        previously only described as an external expectation: "wrap
        this tenant's OAuth credential under a KMS key scoped to that
        tenant alone, decrypted only inside that tenant's own compute
        cell -- this codebase's `TenantJiraConfig` accepts a plain
        token/secret because *how* it is fetched/decrypted is ... out-
        side D4's own scope." `kwargs` must include `tenant_id` (and
        `base_url`, and any other field the plain constructor would
        otherwise need) -- it is what selects which tenant's KMS key
        context unwraps `wrapped_oauth_token`; `kms_boundary`
        fail-closed rejects (with `kms_boundary.
        CrossTenantDecryptionError`) an attempt to unwrap a token that
        was wrapped for a different tenant. `auth_mode` defaults to
        `"oauth_bearer"` (this constructor makes no sense for
        `"basic"` auth) but may be overridden via `kwargs` if a caller
        has a reason to.
        """
        tenant_id = kwargs.get("tenant_id")
        if not tenant_id:
            raise ValueError("tenant_id is required")
        oauth_bearer_token = kms_boundary.decrypt(str(tenant_id), wrapped_oauth_token).decode("utf-8")
        kwargs.setdefault("auth_mode", "oauth_bearer")
        return cls(oauth_bearer_token=oauth_bearer_token, **kwargs)  # type: ignore[arg-type]
