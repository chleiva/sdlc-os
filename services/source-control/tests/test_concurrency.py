"""Real concurrency proof for the D5 acceptance criterion: "Two
concurrent branch-creation calls for the same run never produce
overlapping worktrees."

Threads racing against the *same* fixture repo, each one shelling out to
a real `git` subprocess -- this is genuine OS-level concurrency (git
itself runs as a separate process per call), not a simulated race.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from source_control import git_ops
from source_control.git_ops import BranchAlreadyExists


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def test_two_concurrent_calls_for_the_same_session_never_double_create(git_fixture_repo: Path, tmp_path: Path):
    """Same run/session racing itself (e.g. a retried call) -- exactly
    one worktree must exist afterward, never two, and git's own
    worktree metadata must never be corrupted by the race."""
    worktrees_root = tmp_path / "worktrees"
    outcomes: list = []
    errors: list = []
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        try:
            r = git_ops.create_branch_worktree(
                repo_path=git_fixture_repo, base_ref="main", branch_name="feature/race-same-session",
                session_id="session-race", worktrees_root=worktrees_root,
            )
            outcomes.append(("ok", r))
        except BranchAlreadyExists as e:
            outcomes.append(("already-exists", e))
        except Exception as e:  # pragma: no cover - failure path we assert against
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == [], f"no thread should raise an unexpected exception, got: {errors}"
    assert len(outcomes) == 2

    # Both calls used the SAME session_id -> the worktree path is
    # identical for both, so this is the idempotent-retry case: both
    # calls succeed with identical results (the lock serializes them, the
    # second sees "already exists as *my* worktree" and returns the same
    # data), never one "ok" + one silently-corrupting duplicate create.
    ok_results = [r for kind, r in outcomes if kind == "ok"]
    assert len(ok_results) == 2
    assert ok_results[0] == ok_results[1]

    # git's own metadata shows exactly one worktree for this branch.
    listing = _git(["worktree", "list", "--porcelain"], git_fixture_repo).stdout
    assert listing.count("branch refs/heads/feature/race-same-session") == 1


def test_two_concurrent_calls_different_sessions_never_overlap_worktrees(git_fixture_repo: Path, tmp_path: Path):
    """Different sessions racing concurrently on the same repo -- each
    must get its own, fully-checked-out, non-overlapping worktree; no
    thread may observe or corrupt another's in-progress checkout."""
    worktrees_root = tmp_path / "worktrees"
    n = 8
    results: list = [None] * n
    errors: list = []
    barrier = threading.Barrier(n)

    def worker(i: int):
        barrier.wait()
        try:
            results[i] = git_ops.create_branch_worktree(
                repo_path=git_fixture_repo, base_ref="main", branch_name=f"feature/race-{i}",
                session_id=f"session-{i}", worktrees_root=worktrees_root,
            )
        except Exception as e:  # pragma: no cover
            errors.append((i, e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == [], f"no session's create call should fail: {errors}"
    assert all(r is not None for r in results)

    paths = [r.worktree_path for r in results]
    assert len(set(paths)) == n, "every session must get a distinct, non-overlapping worktree path"

    for r in results:
        wt = Path(r.worktree_path)
        assert wt.is_dir()
        assert (wt / "README.md").exists(), f"worktree {wt} was not fully/correctly checked out"

    # git worktree metadata agrees: N+1 worktrees registered (N new + the
    # main checkout), none sharing a path, none half-initialized.
    listing = _git(["worktree", "list", "--porcelain"], git_fixture_repo).stdout
    worktree_lines = [line for line in listing.splitlines() if line.startswith("worktree ")]
    assert len(worktree_lines) == n + 1
    assert len(set(worktree_lines)) == n + 1  # no duplicates


def test_race_never_leaves_a_half_created_worktree_directory(git_fixture_repo: Path, tmp_path: Path):
    """Stress the same session_id from many threads at once; afterward
    the on-disk worktree must be exactly one, fully populated directory
    -- never a partially-written one from an interrupted concurrent
    `git worktree add`."""
    worktrees_root = tmp_path / "worktrees"
    n = 6
    outcomes: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()
        try:
            r = git_ops.create_branch_worktree(
                repo_path=git_fixture_repo, base_ref="main", branch_name="feature/stress",
                session_id="session-stress", worktrees_root=worktrees_root,
            )
            with lock:
                outcomes.append(("ok", r))
        except BranchAlreadyExists as e:
            with lock:
                outcomes.append(("already-exists", e))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(outcomes) == n
    ok = [r for kind, r in outcomes if kind == "ok"]
    assert len(ok) == n  # same session_id => idempotent success every time
    assert len({r.worktree_path for r in ok}) == 1
    worktree_path = Path(ok[0].worktree_path)
    assert (worktree_path / "README.md").exists()
    assert (worktree_path / ".git").exists()
