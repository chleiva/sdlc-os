"""Real `git worktree add`/branch creation against a local git repo
fixture (spec Section 8.1: one isolated worktree per agent/session).
No GitHub account or network involved -- this is the part of D5 the
brief says is testable for real with no external account."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from source_control import git_ops
from source_control.git_ops import BranchAlreadyExists, GitOpsError


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def test_create_branch_worktree_creates_a_real_worktree_and_branch(git_fixture_repo: Path, tmp_path: Path):
    worktrees_root = tmp_path / "worktrees"
    result = git_ops.create_branch_worktree(
        repo_path=git_fixture_repo, base_ref="main", branch_name="feature/session-1",
        session_id="session-1", worktrees_root=worktrees_root,
    )

    assert result.branch_name == "feature/session-1"
    assert result.base_ref == "main"
    assert len(result.sha) == 40
    worktree_path = Path(result.worktree_path)
    assert worktree_path.is_dir()
    assert (worktree_path / "README.md").exists()  # real checkout, not a stub

    # The branch really exists in the repo, at the expected commit.
    show_ref = _git(["rev-parse", "refs/heads/feature/session-1"], git_fixture_repo)
    assert show_ref.stdout.strip() == result.sha

    # And git itself considers this a real, registered worktree.
    listing = _git(["worktree", "list", "--porcelain"], git_fixture_repo).stdout
    assert str(worktree_path) in listing


def test_worktree_path_is_keyed_by_session_id_one_per_session(git_fixture_repo: Path, tmp_path: Path):
    worktrees_root = tmp_path / "worktrees"
    r1 = git_ops.create_branch_worktree(
        repo_path=git_fixture_repo, base_ref="main", branch_name="feature/a",
        session_id="session-a", worktrees_root=worktrees_root,
    )
    r2 = git_ops.create_branch_worktree(
        repo_path=git_fixture_repo, base_ref="main", branch_name="feature/b",
        session_id="session-b", worktrees_root=worktrees_root,
    )
    assert r1.worktree_path != r2.worktree_path
    assert Path(r1.worktree_path).is_dir() and Path(r2.worktree_path).is_dir()


def test_retrying_the_same_session_and_branch_is_idempotent(git_fixture_repo: Path, tmp_path: Path):
    worktrees_root = tmp_path / "worktrees"
    r1 = git_ops.create_branch_worktree(
        repo_path=git_fixture_repo, base_ref="main", branch_name="feature/retry",
        session_id="session-retry", worktrees_root=worktrees_root,
    )
    r2 = git_ops.create_branch_worktree(
        repo_path=git_fixture_repo, base_ref="main", branch_name="feature/retry",
        session_id="session-retry", worktrees_root=worktrees_root,
    )
    assert r1 == r2


def test_a_different_session_reusing_an_existing_branch_name_is_reported_as_already_existing(
    git_fixture_repo: Path, tmp_path: Path,
):
    worktrees_root = tmp_path / "worktrees"
    git_ops.create_branch_worktree(
        repo_path=git_fixture_repo, base_ref="main", branch_name="feature/shared-name",
        session_id="session-first", worktrees_root=worktrees_root,
    )
    with pytest.raises(BranchAlreadyExists):
        git_ops.create_branch_worktree(
            repo_path=git_fixture_repo, base_ref="main", branch_name="feature/shared-name",
            session_id="session-second", worktrees_root=worktrees_root,
        )


def test_unknown_base_ref_raises_git_ops_error(git_fixture_repo: Path, tmp_path: Path):
    with pytest.raises(GitOpsError):
        git_ops.create_branch_worktree(
            repo_path=git_fixture_repo, base_ref="does-not-exist", branch_name="feature/x",
            session_id="session-x", worktrees_root=tmp_path / "worktrees",
        )


def test_invalid_branch_name_is_rejected(git_fixture_repo: Path, tmp_path: Path):
    with pytest.raises(GitOpsError):
        git_ops.create_branch_worktree(
            repo_path=git_fixture_repo, base_ref="main", branch_name="-rm -rf /",
            session_id="session-y", worktrees_root=tmp_path / "worktrees",
        )


def test_remove_worktree_cleans_up_with_no_trace(git_fixture_repo: Path, tmp_path: Path):
    worktrees_root = tmp_path / "worktrees"
    result = git_ops.create_branch_worktree(
        repo_path=git_fixture_repo, base_ref="main", branch_name="feature/abandon",
        session_id="session-abandon", worktrees_root=worktrees_root,
    )
    git_ops.remove_worktree(repo_path=git_fixture_repo, worktree_path=Path(result.worktree_path))
    listing = _git(["worktree", "list", "--porcelain"], git_fixture_repo).stdout
    assert result.worktree_path not in listing
    assert not Path(result.worktree_path).exists()


def test_build_authenticated_remote_url_uses_x_access_token_never_a_pat_shape():
    url = git_ops.build_authenticated_remote_url("https://github.com/acme/app.git", "ghs_installation_token_value")
    assert url == "https://x-access-token:ghs_installation_token_value@github.com/acme/app.git"


def test_clone_or_update_mirror_works_against_a_local_file_remote(git_fixture_repo: Path, tmp_path: Path):
    """Exercises the real clone/fetch mechanics end-to-end using a local
    file:// remote (no network, no GitHub account) -- proving the
    mechanism `build_authenticated_remote_url` feeds into actually works,
    even though the authenticated-HTTPS form itself can only be proven
    against real github.com by a human with a live installation."""
    mirror_path = tmp_path / "mirror"
    git_ops.clone_or_update_mirror(remote_url=str(git_fixture_repo), local_path=mirror_path)
    assert (mirror_path / "README.md").exists()

    # Second call takes the fetch path, not clone-again.
    git_ops.clone_or_update_mirror(remote_url=str(git_fixture_repo), local_path=mirror_path)
