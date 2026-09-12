"""SourceControlService: the real implementation behind F3's
source-control MCP contract (schema at
services/mcp-stubs/source-control/schema/source-control.schema.json).

Every public method takes `tenant_id` + `repository` and resolves them,
fail-closed, through `InstallationRegistry` to exactly one tenant's
GitHub App installation before doing anything else (spec Section 14.13 /
Section 22's "over-broad grant" mitigation: a tenant/repository pair this
service doesn't recognize is `permission-denied`, never silently routed
anywhere else). Every method returns the plain-dict wire shape from
`result.Result.to_wire()` -- `ok` / `empty` / `error`, never a raw
exception -- exactly matching the F3 schema's `oneOf`.

`create_branch_worktree` is backed by real local `git worktree`
mechanics (git_ops.py); the other four are backed by real GitHub REST
API v3 calls (github_client.py) authenticated as the App installation,
never a personal access token.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kms_boundary import KmsBoundary, WrappedSecret
from source_control import git_ops, webhook
from source_control.audit import AuditLogger
from source_control.errors import NotFoundError, PermissionDeniedError, SourceControlError
from source_control.git_ops import BranchAlreadyExists, GitOpsError
from source_control.github_client import AppCredentials, GitHubAppClient
from source_control.result import Result

_CONCLUSION_MAP = {
    "success": "success",
    "failure": "failure",
    "neutral": "neutral",
    "cancelled": "cancelled",
    "skipped": "neutral",
    "timed_out": "failure",
    "action_required": "failure",
    "stale": "cancelled",
}


def _map_conclusion(raw: str | None) -> str | None:
    if raw is None:
        return None
    return _CONCLUSION_MAP.get(raw, "failure")


@dataclass(frozen=True)
class TenantInstallation:
    """One tenant's concrete GitHub App installation (spec Section
    17.1's NHI, scoped per Section 14.13's per-tenant deployment split).
    `private_key_pem` is expected to already be the plaintext unwrapped
    at the point of use from this tenant's own KMS-wrapped secret
    (Section 17.3) -- nothing in this module wraps/unwraps it or writes
    it to disk."""

    tenant_id: str
    installation_id: str
    app_id: str
    app_slug: str
    private_key_pem: bytes
    allowed_repositories: frozenset[str]
    mirror_root: Path
    api_base_url: str = "https://api.github.com"

    @classmethod
    def from_wrapped_private_key(
        cls,
        *,
        tenant_id: str,
        installation_id: str,
        app_id: str,
        app_slug: str,
        wrapped_private_key: WrappedSecret,
        kms_boundary: KmsBoundary,
        allowed_repositories: frozenset[str],
        mirror_root: Path,
        api_base_url: str = "https://api.github.com",
    ) -> "TenantInstallation":
        """Alternate constructor (additive -- the plain, plaintext-PEM
        constructor above is unchanged and is still what every existing
        test in this suite uses): builds a `TenantInstallation` from a
        `kms_boundary.WrappedSecret` instead of an already-unwrapped
        PEM, by calling `kms_boundary.decrypt(tenant_id, ...)` at this
        exact point of use -- closing the gap this class's own
        docstring previously only described as an external expectation
        ("private_key_pem is expected to already be the plaintext
        unwrapped ... from this tenant's own KMS-wrapped secret",
        Sec. 17.3). `kms_boundary` fail-closed rejects (with
        `kms_boundary.CrossTenantDecryptionError`) an attempt to unwrap
        a secret that was not wrapped for this same `tenant_id` -- see
        `services/kms-boundary/README.md`.
        """
        private_key_pem = kms_boundary.decrypt(tenant_id, wrapped_private_key)
        return cls(
            tenant_id=tenant_id,
            installation_id=installation_id,
            app_id=app_id,
            app_slug=app_slug,
            private_key_pem=private_key_pem,
            allowed_repositories=allowed_repositories,
            mirror_root=mirror_root,
            api_base_url=api_base_url,
        )


class InstallationRegistry:
    """Per-tenant GitHub App installation registry -- the concrete,
    reconcilable NHI inventory entry (spec Section 17.1) and the
    per-repository scoping boundary (spec Section 22's 'over-broad
    GitHub App grant' risk mitigation): a tenant_id that isn't
    registered, or a repository not in that tenant's allow-list, is
    rejected fail-closed as permission-denied -- never silently routed
    to another tenant's installation or a default/broad one."""

    def __init__(self) -> None:
        self._by_tenant: dict[str, TenantInstallation] = {}

    def register(self, installation: TenantInstallation) -> None:
        self._by_tenant[installation.tenant_id] = installation

    def resolve(self, tenant_id: str, repository: str) -> TenantInstallation:
        if not tenant_id:
            raise PermissionDeniedError("tenant_id is required")
        installation = self._by_tenant.get(tenant_id)
        if installation is None:
            raise PermissionDeniedError(f"tenant '{tenant_id}' has no registered GitHub App installation")
        if repository not in installation.allowed_repositories:
            raise PermissionDeniedError(
                f"repository '{repository}' is not in tenant '{tenant_id}' installation's repository scope"
            )
        return installation


class SourceControlService:
    def __init__(self, registry: InstallationRegistry, *, audit_logger: AuditLogger | None = None):
        self._registry = registry
        self._audit = audit_logger or AuditLogger()
        self._clients: dict[str, GitHubAppClient] = {}

    @property
    def audit(self) -> AuditLogger:
        return self._audit

    def _client_for(self, installation: TenantInstallation) -> GitHubAppClient:
        client = self._clients.get(installation.installation_id)
        if client is None:
            client = GitHubAppClient(
                credentials=AppCredentials(
                    app_id=installation.app_id,
                    app_slug=installation.app_slug,
                    private_key_pem=installation.private_key_pem,
                ),
                api_base_url=installation.api_base_url,
                audit_logger=self._audit,
            )
            self._clients[installation.installation_id] = client
        return client

    # ------------------------------------------------------------------
    # installation-revocation webhook handling
    #
    # SECURITY FIX (D10 security-hardening pass, real finding): this
    # service already had `webhook.verify_signature` (real HMAC check)
    # and `webhook.is_installation_revocation_event` (recognizes GitHub's
    # "installation deleted/suspended" events), each unit-tested in
    # isolation, but *nothing* in this service ever called them -- there
    # was no code path connecting an incoming revocation webhook to this
    # service's own cached credentials. Before this fix, a token cached
    # by `InstallationTokenCache` before revocation but not yet past its
    # TTL would still be handed to any caller by `get_token()` until the
    # next live GitHub API call happened to come back 401/403 (the only
    # place `force_evict` was previously called from,
    # `github_client._authed_request`). That satisfies "revocation
    # eventually takes effect" but not Section 17.1's "revocation is
    # verified end-to-end ... a token revoked in name but still honored
    # somewhere is a live incident" -- a still-cached, unexpired token is
    # exactly that gap. This method wires the already-real webhook checks
    # to proactively evict the cached token (and the cached
    # `GitHubAppClient`) the moment a verified revocation is observed,
    # never waiting for the next failed call.
    # ------------------------------------------------------------------
    def handle_installation_webhook(
        self,
        *,
        event_type: str,
        raw_body: bytes,
        payload: dict,
        signature_header: str | None,
        webhook_secret: bytes,
    ) -> dict:
        """Verify (fail-closed, real HMAC -- see `webhook.verify_signature`)
        then, only for a recognized installation-revocation event,
        proactively evict any cached token/client for that installation_id.
        Raises `webhook.WebhookVerificationError` on a bad/missing
        signature, same as D1's Sec. 17.3 gate: rejected before anything
        else is attempted, never merely logged."""
        webhook.verify_signature(raw_body, signature_header, webhook_secret)

        if not webhook.is_installation_revocation_event(event_type, payload):
            return {"outcome": "ignored", "event_type": event_type}

        installation_id = str((payload.get("installation") or {}).get("id", ""))
        if not installation_id:
            return {"outcome": "ignored", "reason": "no installation id in payload"}

        client = self._clients.pop(installation_id, None)
        if client is not None:
            client.token_cache.force_evict(installation_id)
        return {"outcome": "revoked", "installation_id": installation_id}

    @staticmethod
    def _split_repo(repository: str) -> tuple[str, str]:
        owner, _, repo = repository.partition("/")
        if not owner or not repo:
            raise NotFoundError(f"malformed repository identifier: {repository!r}")
        return owner, repo

    # ------------------------------------------------------------------
    # create-branch-worktree
    # ------------------------------------------------------------------
    def create_branch_worktree(
        self, *, tenant_id: str, repository: str, base_ref: str, branch_name: str, session_id: str,
    ) -> dict:
        try:
            installation = self._registry.resolve(tenant_id, repository)
            owner, repo = self._split_repo(repository)
            mirror_path = installation.mirror_root / owner / repo
            worktrees_root = mirror_path / ".worktrees"
            try:
                result = git_ops.create_branch_worktree(
                    repo_path=mirror_path, base_ref=base_ref, branch_name=branch_name,
                    session_id=session_id, worktrees_root=worktrees_root,
                )
            except BranchAlreadyExists as e:
                return Result.empty(
                    f"Branch '{e.branch_name}' already exists pointing at base ref "
                    f"'{e.base_ref}'; no new branch created.",
                ).to_wire()
            except GitOpsError as e:
                # Local git-mechanics failure (e.g. base_ref doesn't
                # exist, or an unexpected worktree-path conflict). Not a
                # GitHub-upstream failure, but reported with the closest
                # of the four named codes: base-ref-not-found reads
                # naturally as not-found; anything else is treated as a
                # transient local-state problem (upstream-unavailable)
                # rather than invented as a fifth error code.
                message = str(e)
                if "does not exist in this repository" in message:
                    raise NotFoundError(message) from e
                from source_control.errors import UpstreamUnavailableError

                raise UpstreamUnavailableError(message) from e
            return Result.ok({
                "branch_name": result.branch_name,
                "base_ref": result.base_ref,
                "sha": result.sha,
                "worktree_path": result.worktree_path,
            }).to_wire()
        except SourceControlError as e:
            return Result.from_exception(e).to_wire()

    # ------------------------------------------------------------------
    # open-pr
    # ------------------------------------------------------------------
    def open_pr(
        self, *, tenant_id: str, repository: str, head_branch: str, base_branch: str,
        title: str, description: str, draft: bool = False,
    ) -> dict:
        try:
            installation = self._registry.resolve(tenant_id, repository)
            owner, repo = self._split_repo(repository)
            client = self._client_for(installation)

            existing = client.list_pulls(
                installation_id=installation.installation_id, tenant_id=tenant_id,
                owner=owner, repo=repo, head=f"{owner}:{head_branch}", base=base_branch, state="open",
            )
            if existing:
                pr = existing[0]
                return Result.empty(
                    f"An open PR already exists for {head_branch} -> {base_branch}; no new PR opened.",
                    existing_pr_number=pr["number"],
                ).to_wire()

            pr = client.create_pull(
                installation_id=installation.installation_id, tenant_id=tenant_id,
                owner=owner, repo=repo, head=head_branch, base=base_branch,
                title=title, body=description, draft=draft,
            )
            return Result.ok({
                "pr_number": pr["number"],
                "url": pr["html_url"],
                "state": "draft" if pr.get("draft") else "open",
            }).to_wire()
        except SourceControlError as e:
            return Result.from_exception(e).to_wire()

    # ------------------------------------------------------------------
    # get-pr-check-status
    # ------------------------------------------------------------------
    def get_pr_check_status(self, *, tenant_id: str, repository: str, pr_number: int) -> dict:
        try:
            installation = self._registry.resolve(tenant_id, repository)
            owner, repo = self._split_repo(repository)
            client = self._client_for(installation)

            pr = client.get_pull(
                installation_id=installation.installation_id, tenant_id=tenant_id,
                owner=owner, repo=repo, number=pr_number,
            )
            sha = pr["head"]["sha"]
            checks_resp = client.list_check_runs_for_ref(
                installation_id=installation.installation_id, tenant_id=tenant_id,
                owner=owner, repo=repo, ref=sha,
            )
            check_runs = checks_resp.get("check_runs", [])
            if not check_runs:
                return Result.empty(
                    f"PR #{pr_number} exists but no checks have been registered against it yet.",
                ).to_wire()

            checks = [
                {
                    "name": c["name"],
                    "status": c["status"],
                    "conclusion": _map_conclusion(c.get("conclusion")),
                    "url": c.get("html_url", pr["html_url"]),
                }
                for c in check_runs
            ]
            if pr.get("merged"):
                state = "merged"
            elif pr["state"] == "closed":
                state = "closed"
            elif pr.get("draft"):
                state = "draft"
            else:
                state = "open"

            return Result.ok({
                "pr_number": pr["number"],
                "state": state,
                "mergeable": bool(pr.get("mergeable")),
                "checks": checks,
            }).to_wire()
        except SourceControlError as e:
            return Result.from_exception(e).to_wire()

    # ------------------------------------------------------------------
    # get-file-contents
    # ------------------------------------------------------------------
    def get_file_contents(self, *, tenant_id: str, repository: str, branch: str, path: str) -> dict:
        try:
            installation = self._registry.resolve(tenant_id, repository)
            owner, repo = self._split_repo(repository)
            client = self._client_for(installation)

            resp = client.get_contents(
                installation_id=installation.installation_id, tenant_id=tenant_id,
                owner=owner, repo=repo, path=path, ref=branch,
            )
            if isinstance(resp, list):
                raise NotFoundError(f"'{path}' is a directory, not a file")

            size = resp.get("size", 0)
            if size == 0:
                return Result.empty(
                    f"'{path}' exists on branch '{branch}' but is empty (zero bytes).",
                ).to_wire()

            encoding = "base64" if resp.get("encoding") == "base64" else "utf-8"
            content = resp.get("content", "")
            if encoding == "base64":
                content = content.replace("\n", "")

            return Result.ok({
                "path": resp["path"],
                "branch": branch,
                "content": content,
                "encoding": encoding,
                "size": size,
                "sha": resp["sha"],
            }).to_wire()
        except SourceControlError as e:
            return Result.from_exception(e).to_wire()

    # ------------------------------------------------------------------
    # list-files
    # ------------------------------------------------------------------
    def list_files(
        self, *, tenant_id: str, repository: str, branch: str,
        path_prefix: str = "", recursive: bool = True,
    ) -> dict:
        try:
            installation = self._registry.resolve(tenant_id, repository)
            owner, repo = self._split_repo(repository)
            client = self._client_for(installation)

            ref_info = client.get_ref(
                installation_id=installation.installation_id, tenant_id=tenant_id,
                owner=owner, repo=repo, ref=f"heads/{branch}",
            )
            commit_sha = ref_info["object"]["sha"]
            tree_resp = client.get_tree(
                installation_id=installation.installation_id, tenant_id=tenant_id,
                owner=owner, repo=repo, tree_sha=commit_sha, recursive=recursive,
            )
            entries = tree_resp.get("tree", [])
            files = []
            for entry in entries:
                p = entry["path"]
                if path_prefix and not p.startswith(path_prefix):
                    continue
                files.append({
                    "path": p,
                    "type": "file" if entry["type"] == "blob" else "directory",
                    "size": entry.get("size") or 0,
                })
            if not files:
                return Result.empty(
                    f"No files matched path prefix '{path_prefix}' on branch '{branch}'.",
                ).to_wire()
            return Result.ok({"files": files, "next_cursor": None}).to_wire()
        except SourceControlError as e:
            return Result.from_exception(e).to_wire()
