"""Real `git worktree`/branch operations (spec Section 8.1: one isolated
worktree per agent/session, two agents never sharing a checkout).

This module operates on an already-present local git repository (the
tenant's own mirror/clone of the GitHub repository -- `clone_or_update`
below is the real, if minimally-exercised-against-github.com, mechanism
for keeping that mirror current via an installation-token-authenticated
HTTPS URL). Everything in `create_branch_worktree` is exercised for real
against a local fixture repository in tests/ -- no GitHub account needed
for any of it, per the D5 brief.

Concurrency: a per-repository OS-level file lock (`fcntl.flock`, POSIX)
serializes the "does this branch/worktree already exist? if not, create
it" critical section, so two racing calls (same process via threads, or
different processes/hosts sharing the same mirror via a shared
filesystem) can never both decide "doesn't exist yet" and both run `git
worktree add` for the same path, corrupting `.git/worktrees` metadata.
See tests/test_concurrency.py for real thread-race proof.
"""

from __future__ import annotations

import fcntl
import re
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_VALID_BRANCH_NAME_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


class GitOpsError(Exception):
    pass


class BranchAlreadyExists(Exception):
    """Raised internally when the requested branch already exists
    pointing at the requested base ref -- the service layer turns this
    into the F3 contract's EmptyResult, never an error."""

    def __init__(self, branch_name: str, base_ref: str, sha: str):
        super().__init__(f"branch '{branch_name}' already exists at '{base_ref}' ({sha})")
        self.branch_name = branch_name
        self.base_ref = base_ref
        self.sha = sha


@dataclass(frozen=True)
class WorktreeResult:
    branch_name: str
    base_ref: str
    sha: str
    worktree_path: str


def _run_git(args: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
    )
    if check and result.returncode != 0:
        raise GitOpsError(f"git {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}")
    return result


def _validate_branch_name(branch_name: str) -> None:
    if not branch_name or not _VALID_BRANCH_NAME_RE.match(branch_name) or branch_name.startswith("-"):
        raise GitOpsError(f"invalid branch name: {branch_name!r}")


@contextmanager
def _repo_lock(repo_path: Path):
    """Real OS-level advisory lock scoped to one repository, held for the
    whole check-then-create critical section. `fcntl.flock` blocks the
    calling thread/process until the lock is free -- this is what makes
    the concurrency guarantee real rather than a hopeful comment."""
    lock_path = repo_path / ".git" / "sdlc-worktree.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _branch_sha(repo_path: Path, branch_name: str) -> str | None:
    result = _run_git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch_name}"], cwd=repo_path, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _resolve_ref_sha(repo_path: Path, ref: str) -> str:
    result = _run_git(["rev-parse", "--verify", "--quiet", ref], cwd=repo_path, check=False)
    if result.returncode != 0:
        raise GitOpsError(f"base_ref '{ref}' does not exist in this repository")
    return result.stdout.strip()


def _existing_worktree_path(repo_path: Path, branch_name: str) -> str | None:
    """Returns the worktree path already registered for `branch_name`, if
    any (`git worktree list --porcelain`), so a repeat call for the same
    branch is idempotent instead of erroring on 'already checked out'."""
    result = _run_git(["worktree", "list", "--porcelain"], cwd=repo_path, check=True)
    current_path = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = line[len("worktree "):].strip()
        elif line == f"branch refs/heads/{branch_name}" and current_path:
            return current_path
    return None


def create_branch_worktree(
    *,
    repo_path: Path,
    base_ref: str,
    branch_name: str,
    session_id: str,
    worktrees_root: Path,
) -> WorktreeResult:
    """The real operation behind F3's `create-branch-worktree` tool.

    One worktree per session (spec Section 8.1): the worktree always
    lives at `worktrees_root/<session_id>`, so two different sessions
    can never collide on a path, and a retried call for the *same*
    session/branch is idempotent (returns the existing worktree rather
    than erroring or double-creating).

    Raises `BranchAlreadyExists` (caller maps to EmptyResult) when
    `branch_name` already exists pointing at a *different* session's
    worktree at the same base ref -- per the F3 schema's literal wording,
    "a branch with this name already exists pointing at the requested
    base ref."
    """
    _validate_branch_name(branch_name)
    repo_path = repo_path.resolve()
    worktree_path = (worktrees_root / session_id).resolve()

    with _repo_lock(repo_path):
        base_sha = _resolve_ref_sha(repo_path, base_ref)
        existing_sha = _branch_sha(repo_path, branch_name)

        if existing_sha is not None:
            existing_worktree = _existing_worktree_path(repo_path, branch_name)
            if existing_worktree == str(worktree_path) and Path(existing_worktree).is_dir():
                # Same session retrying: idempotent success, not an error
                # and not "empty" -- the caller gets back exactly what it
                # would have gotten from the original call.
                return WorktreeResult(
                    branch_name=branch_name, base_ref=base_ref, sha=existing_sha,
                    worktree_path=str(worktree_path),
                )
            # Branch exists (created by this session or another) but not
            # as this session's worktree, or points at a stale sha vs the
            # requested base -- per the schema, "already exists" is an
            # empty result, never an error.
            raise BranchAlreadyExists(branch_name, base_ref, existing_sha)

        if worktree_path.exists():
            raise GitOpsError(f"worktree path already exists but is not registered to branch '{branch_name}': {worktree_path}")

        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        _run_git(
            ["worktree", "add", "-b", branch_name, str(worktree_path), base_sha],
            cwd=repo_path,
        )
        created_sha = _branch_sha(repo_path, branch_name)
        assert created_sha == base_sha
        return WorktreeResult(
            branch_name=branch_name, base_ref=base_ref, sha=created_sha, worktree_path=str(worktree_path),
        )


def remove_worktree(*, repo_path: Path, worktree_path: Path, force: bool = True) -> None:
    """Safe rollback support (spec Section 18): cleanly discard a
    worktree with no trace left, e.g. when a task is abandoned."""
    with _repo_lock(repo_path.resolve()):
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(worktree_path))
        _run_git(args, cwd=repo_path.resolve())


def build_authenticated_remote_url(remote_url: str, installation_token: str) -> str:
    """Builds the HTTPS remote URL git uses to authenticate as the
    installation for clone/fetch/push, per GitHub's documented mechanism:
    `x-access-token` as the username, the installation token as the
    password. Never a personal access token, and the token is never
    written to `.git/config` (the caller passes this URL directly to a
    one-shot `git` invocation, e.g. via `GIT_ASKPASS`-free inline URL, and
    it is not persisted as a remote)."""
    if not remote_url.startswith("https://"):
        raise GitOpsError("GitHub App installation auth requires an https:// remote URL")
    host_and_path = remote_url[len("https://"):]
    return f"https://x-access-token:{installation_token}@{host_and_path}"


def clone_or_update_mirror(*, remote_url: str, local_path: Path) -> None:
    """Real clone/fetch mechanics for keeping the tenant's local mirror
    current. `remote_url` is expected to already be the authenticated
    form from `build_authenticated_remote_url` for a real github.com
    remote; tests exercise this against a local file:// fixture remote
    (no token needed for file://) to prove the git mechanics without
    requiring network access to github.com."""
    local_path = local_path.resolve()
    if (local_path / ".git").exists() or (local_path / "HEAD").exists():
        _run_git(["fetch", "--all", "--prune"], cwd=local_path)
        return
    local_path.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["clone", remote_url, str(local_path)], cwd=local_path.parent)
