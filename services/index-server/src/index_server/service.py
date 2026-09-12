"""IndexService: real implementations of every tool in F3's index
contract (services/mcp-stubs/index/schema/index.schema.json), wired to
the deterministic engine in engine/repo_index.py.

Every method here returns a full envelope dict (outcome: ok/empty/
error) built from index_server.contract.envelope -- the same envelope
builder F3's stubs use -- so the wire shape can never drift from the
stub's own. server.py validates the returned payload against the
tool's output_schema before it goes out, exactly as F3's stub runtime
does.

Error-condition mapping (documented, since none of these are canned):
  * permission-denied -- unknown tenant_id, or a tenant_id not
    authorized for the requested repository (fail-closed, Sec. 14.13;
    also covers "repository exists for some other tenant" without
    revealing that fact).
  * not-found -- the repository IS registered to this tenant, but the
    requested file/path does not exist in it (or the registered repo
    path itself is missing on disk -- a real configuration failure).
  * rate-limited -- this tenant has exceeded the real per-tenant
    request quota (ratelimit.py) -- not a magic tenant id.
  * upstream-unavailable -- the one genuine upstream this fully local
    server has: git. Raised when get-ownership-metadata needs git log
    and git itself fails (not installed, corrupt repo, etc).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .engine import git_utils
from .engine.model import CallSite, Definition, Reference
from .engine.repo_index import RepoIndex
from .contract import envelope
from .ratelimit import RateLimiter
from .tenants import TenantRegistry


class ToolError(Exception):
    def __init__(self, code: str, message: str | None = None, retry_after: int | None = None, details: dict | None = None):
        super().__init__(message or code)
        self.code = code
        self.message = message
        self.retry_after = retry_after
        self.details = details


def _definition_dict(d: Definition) -> dict:
    return {"file": d.file, "line": d.line, "column": d.column, "symbol": d.name, "kind": d.kind, "snippet": d.snippet}


def _reference_dict(r: Reference) -> dict:
    return {"file": r.file, "line": r.line, "column": r.column, "context_snippet": r.context_snippet}


def _caller_dict(c: CallSite) -> dict:
    return {
        "file": c.file,
        "line": c.line,
        "call_site_symbol": c.callee_bare_name,
        "containing_symbol": c.containing_symbol_name,
    }


class IndexService:
    def __init__(self, tenants: TenantRegistry | None = None, rate_limiter: RateLimiter | None = None):
        self.tenants = tenants or TenantRegistry()
        self.rate_limiter = rate_limiter or RateLimiter()
        self._indexes: dict[Path, RepoIndex] = {}

    # -- infra --------------------------------------------------------------
    def _resolve_repo(self, tenant_id: str, repository: str) -> RepoIndex:
        if not self.tenants.is_known_tenant(tenant_id):
            raise ToolError("permission-denied", f"Unknown tenant '{tenant_id}'.")
        path = self.tenants.repo_path(tenant_id, repository)
        if path is None:
            raise ToolError(
                "permission-denied",
                f"Tenant '{tenant_id}' is not authorized for repository '{repository}'.",
            )
        if not path.is_dir():
            raise ToolError("not-found", f"Repository '{repository}' is registered but its path does not exist.")
        idx = self._indexes.get(path)
        if idx is None:
            idx = RepoIndex(path, repository)
            idx.build()
            self._indexes[path] = idx
        return idx

    def refresh_repo(self, tenant_id: str, repository: str):
        """Exposed for callers (and tests) that want to force an
        incremental refresh explicitly -- e.g. a post-commit webhook.
        Not one of F3's six tools; an internal operational hook."""
        idx = self._resolve_repo(tenant_id, repository)
        return idx.refresh()

    def _call(self, tenant_id: str, fn: Callable[[], dict]) -> dict:
        allowed, retry_after = self.rate_limiter.check(tenant_id)
        if not allowed:
            return envelope.error("rate-limited", retry_after_seconds=retry_after)
        try:
            return fn()
        except ToolError as e:
            return envelope.error(e.code, message=e.message, retry_after_seconds=e.retry_after, details=e.details)
        except git_utils.GitUnavailable as e:
            return envelope.error("upstream-unavailable", message=str(e))

    # -- tools ----------------------------------------------------------------
    def find_definition(self, args: dict) -> dict:
        def fn():
            idx = self._resolve_repo(args["tenant_id"], args["repository"])
            defs = idx.find_definition(args["symbol"], args.get("origin"))
            if not defs:
                return envelope.empty(
                    f"No definition found for symbol '{args['symbol']}' in repository '{args['repository']}'."
                )
            return envelope.ok({"definitions": [_definition_dict(d) for d in defs]})

        return self._call(args["tenant_id"], fn)

    def find_references(self, args: dict) -> dict:
        def fn():
            idx = self._resolve_repo(args["tenant_id"], args["repository"])
            refs, known = idx.find_references(
                args["symbol"], args.get("origin"), args.get("include_test_files", True)
            )
            if not refs:
                reason = (
                    f"Symbol '{args['symbol']}' has zero references anywhere in the index."
                    if known
                    else f"Symbol '{args['symbol']}' was not found in the index."
                )
                return envelope.empty(reason, exhaustive=True)
            return envelope.ok({"references": [_reference_dict(r) for r in refs], "total_count": len(refs), "exhaustive": True})

        return self._call(args["tenant_id"], fn)

    def find_callers(self, args: dict) -> dict:
        def fn():
            idx = self._resolve_repo(args["tenant_id"], args["repository"])
            calls, next_cursor, known = idx.find_callers(
                args["symbol"], args.get("origin"), args.get("cursor"), args.get("max_results", 100)
            )
            if not calls:
                return envelope.empty(f"'{args['symbol']}' has no callers anywhere in the index.")
            return envelope.ok({"hop": 1, "callers": [_caller_dict(c) for c in calls], "next_cursor": next_cursor})

        return self._call(args["tenant_id"], fn)

    def search(self, args: dict) -> dict:
        def fn():
            query = args["query"]
            max_results = args.get("max_results", 20)
            repository = args.get("repository")
            results: list[dict] = []
            if repository:
                idx = self._resolve_repo(args["tenant_id"], repository)
                results = idx.search(query, max_results)
            else:
                for repo_name in self.tenants.repositories_for(args["tenant_id"]):
                    idx = self._resolve_repo(args["tenant_id"], repo_name)
                    results.extend(idx.search(query, max_results))
                results.sort(key=lambda r: -r["score"])
                results = results[:max_results]
            if not results:
                return envelope.empty(
                    f"No candidates matched query '{query}'.", authoritative=False, label="non-authoritative"
                )
            return envelope.ok({"results": results, "authoritative": False, "label": "non-authoritative"})

        return self._call(args["tenant_id"], fn)

    def get_file_module_summary(self, args: dict) -> dict:
        def fn():
            idx = self._resolve_repo(args["tenant_id"], args["repository"])
            path = args["path"]
            full_path = idx.root / path
            if not full_path.is_file():
                raise ToolError("not-found", f"'{path}' does not exist in repository '{args['repository']}'.")
            if full_path.stat().st_size == 0:
                return envelope.empty(f"'{path}' is indexed but empty (zero bytes) -- nothing to summarize.")
            result = idx.file_summary(path)
            summary, symbol_count, generated = result
            return envelope.ok({"path": path, "summary": summary, "symbol_count": symbol_count, "generated": generated})

        return self._call(args["tenant_id"], fn)

    def get_ownership_metadata(self, args: dict) -> dict:
        def fn():
            idx = self._resolve_repo(args["tenant_id"], args["repository"])
            path = args["path"]
            if not (idx.root / path).is_file():
                raise ToolError("not-found", f"'{path}' does not exist in repository '{args['repository']}'.")
            result = idx.ownership(path)
            if result is None:
                return envelope.empty(f"'{path}' has no CODEOWNERS entry and no commit history (untracked/new file).")
            return envelope.ok({"path": path, **result})

        return self._call(args["tenant_id"], fn)
