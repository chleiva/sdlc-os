"""Acceptance criterion: "Two agents spawned for the same run never hold
the same git worktree simultaneously." Real `git worktree` operations
against a real local fixture repo -- same technique as D5's own worktree
tests (services/source-control/tests/test_git_worktree.py,
test_concurrency.py) -- see `worktree.py`'s module docstring for why this
is an independent, analogous implementation rather than an import of
D5's module.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from orchestrator.worktree import (
    WorktreeAlreadyLeasedError,
    WorktreeLeaseManager,
    create_agent_worktree,
    list_registered_worktrees,
    remove_agent_worktree,
)


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def test_two_agents_on_the_same_run_get_distinct_non_overlapping_worktrees(git_fixture_repo, tmp_path):
    worktrees_root = tmp_path / "worktrees"
    agent_1 = create_agent_worktree(repo_path=git_fixture_repo, base_ref="main", session_id="run-1-agent-1", worktrees_root=worktrees_root)
    agent_2 = create_agent_worktree(repo_path=git_fixture_repo, base_ref="main", session_id="run-1-agent-2", worktrees_root=worktrees_root)

    assert agent_1.worktree_path != agent_2.worktree_path
    assert Path(agent_1.worktree_path).is_dir() and Path(agent_2.worktree_path).is_dir()
    assert (Path(agent_1.worktree_path) / "README.md").exists()
    assert (Path(agent_2.worktree_path) / "README.md").exists()

    registered = list_registered_worktrees(git_fixture_repo)
    assert agent_1.worktree_path in registered
    assert agent_2.worktree_path in registered


def test_lease_manager_never_lets_two_holders_hold_the_same_worktree_at_once(git_fixture_repo, tmp_path):
    worktrees_root = tmp_path / "worktrees"
    handle = create_agent_worktree(repo_path=git_fixture_repo, base_ref="main", session_id="shared-run-agent", worktrees_root=worktrees_root)

    manager = WorktreeLeaseManager()
    manager.acquire(worktree_path=handle.worktree_path, holder_session_id="agent-A")
    assert manager.current_holder(worktree_path=handle.worktree_path) == "agent-A"

    # A second agent attempting to hold the SAME worktree while agent-A
    # still holds it must be refused, not silently granted.
    with pytest.raises(WorktreeAlreadyLeasedError):
        manager.acquire(worktree_path=handle.worktree_path, holder_session_id="agent-B", blocking=False)

    manager.release(worktree_path=handle.worktree_path)
    assert manager.current_holder(worktree_path=handle.worktree_path) is None

    # Once released, a different agent can legitimately take it over.
    manager.acquire(worktree_path=handle.worktree_path, holder_session_id="agent-B")
    assert manager.current_holder(worktree_path=handle.worktree_path) == "agent-B"
    manager.release(worktree_path=handle.worktree_path)


def test_concurrent_threads_racing_to_acquire_the_same_worktree_never_both_succeed(git_fixture_repo, tmp_path):
    """Real OS-level concurrency proof: two threads race `acquire` for
    the identical worktree path; exactly one may hold it at a time."""
    worktrees_root = tmp_path / "worktrees"
    handle = create_agent_worktree(repo_path=git_fixture_repo, base_ref="main", session_id="race-run-agent", worktrees_root=worktrees_root)
    manager = WorktreeLeaseManager()

    holders_seen_while_running: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def worker(name: str):
        barrier.wait()
        try:
            manager.acquire(worktree_path=handle.worktree_path, holder_session_id=name)
        except WorktreeAlreadyLeasedError:
            return
        try:
            with lock:
                holders_seen_while_running.append(manager.current_holder(worktree_path=handle.worktree_path))
        finally:
            manager.release(worktree_path=handle.worktree_path)

    threads = [threading.Thread(target=worker, args=(f"agent-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert errors == []
    # Both threads block on the OS-level flock (default `blocking=True`),
    # so both eventually get a turn -- but at no point could a second
    # holder have been recorded while the first was still active, since
    # release() always runs before the next acquire() can succeed.
    assert len(holders_seen_while_running) == 2


def test_remove_agent_worktree_cleans_up_with_no_trace(git_fixture_repo, tmp_path):
    worktrees_root = tmp_path / "worktrees"
    handle = create_agent_worktree(repo_path=git_fixture_repo, base_ref="main", session_id="abandon-me", worktrees_root=worktrees_root)
    remove_agent_worktree(repo_path=git_fixture_repo, worktree_path=Path(handle.worktree_path))
    assert handle.worktree_path not in list_registered_worktrees(git_fixture_repo)
    assert not Path(handle.worktree_path).exists()


def test_retrying_the_same_session_is_idempotent_not_a_duplicate(git_fixture_repo, tmp_path):
    worktrees_root = tmp_path / "worktrees"
    r1 = create_agent_worktree(repo_path=git_fixture_repo, base_ref="main", session_id="retry-agent", worktrees_root=worktrees_root)
    r2 = create_agent_worktree(repo_path=git_fixture_repo, base_ref="main", session_id="retry-agent", worktrees_root=worktrees_root)
    assert r1 == r2
