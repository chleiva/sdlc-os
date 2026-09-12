"""Git-worktree-per-agent isolation (master spec Section 8.1): "one agent
owns write access to a given file/module at a time, enforced via an
isolated git worktree per agent/session -- never two agents on the same
checkout."

This mirrors the real technique D5 (source-control integration) built and
tested for its own `create-branch-worktree` F3 tool
(`services/source-control/src/source_control/git_ops.py`): a real local
git repository, real `git worktree add` calls, and a real OS-level
`fcntl.flock` serializing the check-then-create critical section so a
race can never corrupt `.git/worktrees` metadata or hand two callers the
same path. D2 does not import D5's module directly (D2's brief lists F3
as its only Wave-1-sibling dependency, not D5 -- importing another
sibling deliverable's internals would be exactly the kind of unreviewed
cross-deliverable coupling the repo's ground rules warn against), so this
is an independent, analogous implementation scoped to the orchestrator's
own needs: create-worktree-per-session, PLUS a held *lease* for the
lifetime of an agent's actual work in it (D5's lock only serializes
*creation*; D2 additionally needs "two agents never hold the same
worktree simultaneously" for the entire time one is using it, which is a
different, longer-lived exclusion window -- see `WorktreeLeaseManager`).
"""

from __future__ import annotations

import fcntl
import re
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_VALID_BRANCH_NAME_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


class WorktreeError(Exception):
    pass


class WorktreeAlreadyLeasedError(Exception):
    """Raised by `WorktreeLeaseManager.acquire` (non-blocking mode) when
    another agent/session already holds the lease for this worktree."""


@dataclass(frozen=True)
class WorktreeHandle:
    session_id: str
    branch_name: str
    base_ref: str
    sha: str
    worktree_path: str


def _run_git(args: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if check and result.returncode != 0:
        raise WorktreeError(f"git {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}")
    return result


def _validate_branch_name(branch_name: str) -> None:
    if not branch_name or not _VALID_BRANCH_NAME_RE.match(branch_name) or branch_name.startswith("-"):
        raise WorktreeError(f"invalid branch name: {branch_name!r}")


@contextmanager
def _repo_lock(repo_path: Path):
    lock_path = repo_path / ".git" / "orchestrator-worktree.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _resolve_ref_sha(repo_path: Path, ref: str) -> str:
    result = _run_git(["rev-parse", "--verify", "--quiet", ref], cwd=repo_path, check=False)
    if result.returncode != 0:
        raise WorktreeError(f"base_ref '{ref}' does not exist in this repository")
    return result.stdout.strip()


def _branch_sha(repo_path: Path, branch_name: str) -> str | None:
    result = _run_git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch_name}"], cwd=repo_path, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def create_agent_worktree(
    *, repo_path: Path, base_ref: str, session_id: str, worktrees_root: Path
) -> WorktreeHandle:
    """One worktree per session_id, at `worktrees_root/<session_id>` on
    branch `agent/<session_id>` -- so two different sessions can never
    collide on a path or a branch name by construction, and a retried
    call for the same session is idempotent."""
    _validate_branch_name(f"agent/{session_id}")
    repo_path = repo_path.resolve()
    branch_name = f"agent/{session_id}"
    worktree_path = (worktrees_root / session_id).resolve()

    with _repo_lock(repo_path):
        base_sha = _resolve_ref_sha(repo_path, base_ref)
        existing_sha = _branch_sha(repo_path, branch_name)
        if existing_sha is not None:
            if worktree_path.is_dir():
                return WorktreeHandle(
                    session_id=session_id, branch_name=branch_name, base_ref=base_ref,
                    sha=existing_sha, worktree_path=str(worktree_path),
                )
            raise WorktreeError(f"branch '{branch_name}' exists but its worktree at {worktree_path} does not")

        if worktree_path.exists():
            raise WorktreeError(f"worktree path already exists but is not registered to branch '{branch_name}': {worktree_path}")

        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["worktree", "add", "-b", branch_name, str(worktree_path), base_sha], cwd=repo_path)
        created_sha = _branch_sha(repo_path, branch_name)
        assert created_sha == base_sha
        return WorktreeHandle(
            session_id=session_id, branch_name=branch_name, base_ref=base_ref,
            sha=created_sha, worktree_path=str(worktree_path),
        )


def remove_agent_worktree(*, repo_path: Path, worktree_path: Path, force: bool = True) -> None:
    with _repo_lock(repo_path.resolve()):
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(worktree_path))
        _run_git(args, cwd=repo_path.resolve())


def list_registered_worktrees(repo_path: Path) -> list[str]:
    result = _run_git(["worktree", "list", "--porcelain"], cwd=repo_path.resolve(), check=True)
    return [line[len("worktree "):].strip() for line in result.stdout.splitlines() if line.startswith("worktree ")]


class WorktreeLeaseManager:
    """Enforces "two agents never hold the same git worktree
    simultaneously" for the full duration an agent is actively using a
    worktree -- a longer-lived exclusion than the create-time lock above.

    Real OS-level enforcement: each lease is backed by an
    exclusively-`flock`'d file handle held open for the lease's lifetime
    (`acquire()` .. `release()`), inside one process or across threads.
    `acquire(blocking=True)` (the default) blocks until free, exactly like
    a real mutex; `acquire(blocking=False)` raises
    `WorktreeAlreadyLeasedError` immediately if another holder is active,
    which is what a test proving mutual exclusion wants to observe.
    """

    def __init__(self) -> None:
        self._process_lock = threading.Lock()
        self._held: dict[str, str] = {}  # worktree_path -> holder session_id
        self._fh_by_path: dict[str, Any] = {}

    def _lease_lock_path(self, worktree_path: str) -> Path:
        return Path(worktree_path).parent / f".lease-{Path(worktree_path).name}.lock"

    def acquire(self, *, worktree_path: str, holder_session_id: str, blocking: bool = True) -> None:
        lock_path = self._lease_lock_path(worktree_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "w")
        flags = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fcntl.flock(fh.fileno(), flags)
        except BlockingIOError as exc:
            fh.close()
            raise WorktreeAlreadyLeasedError(
                f"worktree '{worktree_path}' is already leased to another agent"
            ) from exc

        with self._process_lock:
            existing_holder = self._held.get(worktree_path)
            if existing_holder is not None and existing_holder != holder_session_id:
                # Should be unreachable given the flock above succeeded,
                # but fail loudly rather than silently double-holding.
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                fh.close()
                raise WorktreeAlreadyLeasedError(
                    f"worktree '{worktree_path}' is already leased to session {existing_holder!r}"
                )
            self._held[worktree_path] = holder_session_id
            self._fh_by_path[worktree_path] = fh

    def release(self, *, worktree_path: str) -> None:
        with self._process_lock:
            fh = self._fh_by_path.pop(worktree_path, None)
            self._held.pop(worktree_path, None)
        if fh is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()

    def current_holder(self, *, worktree_path: str) -> str | None:
        with self._process_lock:
            return self._held.get(worktree_path)
