"""D10 adversarial pass on D3 (index server) tenant isolation.

Drives the REAL `index_server.service.IndexService` / `index_server.tenants
.TenantRegistry` -- not a mock. Builds two genuinely separate tenants
with distinct fixture repos and attempts every parameter that could
name data outside the calling tenant's own repository:

  * a `repository` key belonging to a different tenant (expected: was
    already correctly blocked -- confirms the existing design)
  * a `path` argument using `..` traversal or an absolute path to escape
    the resolved repo root and read another tenant's file (this pass
    FOUND this as a real, unpatched gap and fixed it in
    `index_server/service.py::_safe_join`, wired into
    `get_file_module_summary` / `get_ownership_metadata`) -- this test
    proves the fix actually holds, not just that the helper exists.
"""
from __future__ import annotations

import json
import os

import pytest

from index_server.service import IndexService
from index_server.tenants import TenantRegistry


def _make_two_tenants(tmp_path):
    tenant_a_dir = tmp_path / "tenant_a_repo"
    tenant_b_dir = tmp_path / "tenant_b_repo"
    tenant_a_dir.mkdir()
    tenant_b_dir.mkdir()
    (tenant_a_dir / "public.py").write_text("def public_a():\n    return 1\n")
    (tenant_b_dir / "secret.py").write_text("SECRET_TOKEN = 'tenant-b-only-value'\n")

    config_path = tmp_path / "tenants.json"
    config_path.write_text(
        json.dumps(
            {
                "tenant-a": {"repositories": {"repo-a": str(tenant_a_dir)}},
                "tenant-b": {"repositories": {"repo-b": str(tenant_b_dir)}},
            }
        )
    )
    svc = IndexService(tenants=TenantRegistry(config_path))
    return svc, tenant_a_dir, tenant_b_dir


def test_cross_tenant_repository_key_is_denied(tmp_path):
    svc, _, _ = _make_two_tenants(tmp_path)
    payload = svc.get_file_module_summary({"tenant_id": "tenant-a", "repository": "repo-b", "path": "secret.py"})
    assert payload["outcome"] == "error"
    assert payload["error"]["code"] == "permission-denied"


def test_relative_path_traversal_is_blocked_not_leaked(tmp_path):
    """REGRESSION TEST for the real gap this pass found & fixed:
    `path="../tenant_b_repo/secret.py"` previously escaped tenant A's
    repo root via plain pathlib joining with no containment check."""
    svc, tenant_a_dir, tenant_b_dir = _make_two_tenants(tmp_path)
    traversal = os.path.relpath(tenant_b_dir / "secret.py", tenant_a_dir)
    assert traversal.startswith("..")  # sanity: this really is a traversal path

    payload = svc.get_file_module_summary({"tenant_id": "tenant-a", "repository": "repo-a", "path": traversal})
    assert payload["outcome"] == "error"
    assert payload["error"]["code"] == "not-found"
    assert "tenant-b-only-value" not in json.dumps(payload)


def test_absolute_path_escape_is_blocked_not_leaked(tmp_path):
    """REGRESSION TEST: pathlib's `Path(root) / "/etc/passwd"` silently
    discards `root` when the right-hand side is itself absolute -- this
    previously let an absolute `path` argument read anything the
    process could read, entirely bypassing `_resolve_repo`'s tenant
    check."""
    svc, tenant_a_dir, tenant_b_dir = _make_two_tenants(tmp_path)
    abs_secret = str(tenant_b_dir / "secret.py")
    assert os.path.isabs(abs_secret)

    payload = svc.get_file_module_summary({"tenant_id": "tenant-a", "repository": "repo-a", "path": abs_secret})
    assert payload["outcome"] == "error"
    assert payload["error"]["code"] == "not-found"
    assert "tenant-b-only-value" not in json.dumps(payload)


def test_absolute_path_escape_to_arbitrary_host_file_is_blocked(tmp_path):
    """Even a file with no relationship to any tenant (e.g. this
    server's own tenant config, which lists every tenant's repo paths)
    must not be readable via an absolute `path` argument."""
    svc, tenant_a_dir, _ = _make_two_tenants(tmp_path)
    host_file = tmp_path / "host_only_secret.txt"
    host_file.write_text("root-level-secret-outside-any-tenant-repo")

    payload = svc.get_file_module_summary({"tenant_id": "tenant-a", "repository": "repo-a", "path": str(host_file)})
    assert payload["outcome"] == "error"
    assert payload["error"]["code"] == "not-found"
    assert "root-level-secret" not in json.dumps(payload)


def test_ownership_metadata_path_traversal_is_also_blocked(tmp_path):
    """`get_ownership_metadata` has the same unvalidated `path` join as
    `get_file_module_summary` -- confirm the fix covers both tools."""
    svc, tenant_a_dir, tenant_b_dir = _make_two_tenants(tmp_path)
    traversal = os.path.relpath(tenant_b_dir / "secret.py", tenant_a_dir)

    payload = svc.get_ownership_metadata({"tenant_id": "tenant-a", "repository": "repo-a", "path": traversal})
    assert payload["outcome"] == "error"
    assert payload["error"]["code"] == "not-found"


def test_legitimate_relative_path_within_repo_still_works(tmp_path):
    """Regression guard: the containment fix must not break ordinary,
    legitimate in-repo relative paths."""
    svc, _, _ = _make_two_tenants(tmp_path)
    payload = svc.get_file_module_summary({"tenant_id": "tenant-a", "repository": "repo-a", "path": "public.py"})
    assert payload["outcome"] == "ok"
    assert payload["data"]["path"] == "public.py"


def test_dot_slash_and_nested_legitimate_paths_still_work(tmp_path):
    """A relative path with a harmless leading './' or nested-but-still
    -contained '..'-then-back-in traversal (net result still inside
    root) must still resolve -- the fix checks final containment, not
    literal absence of '..' segments."""
    svc, tenant_a_dir, _ = _make_two_tenants(tmp_path)
    (tenant_a_dir / "sub").mkdir()
    (tenant_a_dir / "sub" / "nested.py").write_text("x = 1\n")

    payload = svc.get_file_module_summary({"tenant_id": "tenant-a", "repository": "repo-a", "path": "./public.py"})
    assert payload["outcome"] == "ok"

    payload2 = svc.get_file_module_summary(
        {"tenant_id": "tenant-a", "repository": "repo-a", "path": "sub/../public.py"}
    )
    assert payload2["outcome"] == "ok"


def test_unknown_tenant_search_across_all_repos_never_reaches_another_tenants_repo(tmp_path):
    """`search` without a `repository` iterates `tenants.repositories_for
    (tenant_id)` -- confirm tenant B's repo is never included when
    searching as tenant A, even with a query guaranteed to match tenant
    B's content."""
    svc, _, _ = _make_two_tenants(tmp_path)
    payload = svc.search({"tenant_id": "tenant-a", "query": "SECRET_TOKEN"})
    assert payload["outcome"] in ("empty", "ok")
    if payload["outcome"] == "ok":
        for r in payload["data"]["results"]:
            assert "tenant_b" not in r["file"] and "secret.py" != r["file"]
