"""SourceControlService.create_branch_worktree end-to-end: tenant/repo
resolution through InstallationRegistry, backed by the real git_ops
worktree mechanics against a local fixture repo standing in for the
tenant's already-cloned mirror."""

from __future__ import annotations

import shutil
from pathlib import Path

from source_control.audit import AuditLogger
from source_control.service import InstallationRegistry, SourceControlService, TenantInstallation

TENANT = "tenant-acme"
REPO = "acme/app"


def _install_mirror(git_fixture_repo: Path, mirror_root: Path) -> None:
    dest = mirror_root / "acme" / "app"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(git_fixture_repo, dest)


def _make_service(git_fixture_repo: Path, tmp_path: Path, private_pem: bytes) -> SourceControlService:
    mirror_root = tmp_path / "mirrors"
    _install_mirror(git_fixture_repo, mirror_root)
    installation = TenantInstallation(
        tenant_id=TENANT, installation_id="inst-1", app_id="918273", app_slug="sdlc-auto",
        private_key_pem=private_pem, allowed_repositories=frozenset({REPO}), mirror_root=mirror_root,
    )
    registry = InstallationRegistry()
    registry.register(installation)
    return SourceControlService(registry, audit_logger=AuditLogger())


def test_create_branch_worktree_success(git_fixture_repo, tmp_path, rsa_keypair):
    _, _, private_pem = rsa_keypair
    service = _make_service(git_fixture_repo, tmp_path, private_pem)

    result = service.create_branch_worktree(
        tenant_id=TENANT, repository=REPO, base_ref="main",
        branch_name="feature/agent-session-1", session_id="session-1",
    )
    assert result["outcome"] == "ok"
    assert result["data"]["branch_name"] == "feature/agent-session-1"
    assert Path(result["data"]["worktree_path"]).is_dir()


def test_create_branch_worktree_empty_when_already_exists_for_another_session(git_fixture_repo, tmp_path, rsa_keypair):
    _, _, private_pem = rsa_keypair
    service = _make_service(git_fixture_repo, tmp_path, private_pem)

    service.create_branch_worktree(
        tenant_id=TENANT, repository=REPO, base_ref="main",
        branch_name="feature/shared", session_id="session-a",
    )
    result = service.create_branch_worktree(
        tenant_id=TENANT, repository=REPO, base_ref="main",
        branch_name="feature/shared", session_id="session-b",
    )
    assert result["outcome"] == "empty"
    assert "already exists" in result["reason"]


def test_create_branch_worktree_permission_denied_for_unregistered_tenant(git_fixture_repo, tmp_path, rsa_keypair):
    _, _, private_pem = rsa_keypair
    service = _make_service(git_fixture_repo, tmp_path, private_pem)

    result = service.create_branch_worktree(
        tenant_id="tenant-not-registered", repository=REPO, base_ref="main",
        branch_name="feature/x", session_id="session-1",
    )
    assert result["outcome"] == "error"
    assert result["error"]["code"] == "permission-denied"


def test_create_branch_worktree_permission_denied_for_out_of_scope_repository(git_fixture_repo, tmp_path, rsa_keypair):
    _, _, private_pem = rsa_keypair
    service = _make_service(git_fixture_repo, tmp_path, private_pem)

    result = service.create_branch_worktree(
        tenant_id=TENANT, repository="acme/some-other-repo", base_ref="main",
        branch_name="feature/x", session_id="session-1",
    )
    assert result["outcome"] == "error"
    assert result["error"]["code"] == "permission-denied"


def test_create_branch_worktree_not_found_for_unknown_base_ref(git_fixture_repo, tmp_path, rsa_keypair):
    _, _, private_pem = rsa_keypair
    service = _make_service(git_fixture_repo, tmp_path, private_pem)

    result = service.create_branch_worktree(
        tenant_id=TENANT, repository=REPO, base_ref="does-not-exist",
        branch_name="feature/x", session_id="session-1",
    )
    assert result["outcome"] == "error"
    assert result["error"]["code"] == "not-found"
