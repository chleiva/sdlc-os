"""Thin wrapper around the `git` CLI -- used for (a) change-frequency /
ownership signal and (b) diff-based incremental refresh (which files
changed between the last-indexed commit and HEAD).

This is the one place a genuine "upstream" exists for this otherwise
fully-local, deterministic server: if the repo has no `.git` at all, or
the `git` binary itself fails, that is surfaced as the real
upstream-unavailable condition (service.py), not a canned one.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitUnavailable(RuntimeError):
    """Raised when git plumbing genuinely cannot answer -- not a git
    repo, or the git binary itself failed. Mapped to the upstream-
    unavailable error condition at the service layer."""


def _run(repo_root: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitUnavailable(f"git invocation failed: {exc}") from exc
    if result.returncode != 0:
        raise GitUnavailable(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def is_git_repo(repo_root: Path) -> bool:
    return (repo_root / ".git").exists()


def current_commit(repo_root: Path) -> str:
    return _run(repo_root, ["rev-parse", "HEAD"]).strip()


def changed_files_since(repo_root: Path, since_commit: str) -> tuple[set[str], set[str]]:
    """Returns (changed_or_added, deleted) paths (posix, repo-relative)
    between `since_commit` and HEAD. Used by RepoIndex.refresh() so only
    the files that actually changed get reparsed."""
    out = _run(repo_root, ["diff", "--name-status", since_commit, "HEAD"])
    changed: set[str] = set()
    deleted: set[str] = set()
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status, paths = parts[0], parts[1:]
        if status.startswith("D"):
            deleted.add(paths[0])
        elif status.startswith("R"):
            # rename: old path deleted, new path added.
            deleted.add(paths[0])
            changed.add(paths[1])
        else:
            changed.add(paths[-1])
    return changed, deleted


@dataclass(frozen=True)
class ChangeFrequency:
    commits_last_90d: int
    last_modified: str  # ISO 8601
    top_contributors: list[str]


def change_frequency(repo_root: Path, relative_path: str, since_days: int = 90) -> ChangeFrequency | None:
    """None means the path has no commit history at all (e.g. new/
    untracked file) -- distinct from GitUnavailable (no git at all)."""
    log = _run(
        repo_root,
        [
            "log",
            f"--since={since_days}.days",
            "--format=%an|%aI",
            "--",
            relative_path,
        ],
    )
    lines = [l for l in log.splitlines() if l.strip()]
    if not lines:
        # Fall back to full history for last_modified/top_contributors --
        # a file can be untouched in the last 90 days but still tracked.
        full_log = _run(repo_root, ["log", "--format=%an|%aI", "--", relative_path])
        full_lines = [l for l in full_log.splitlines() if l.strip()]
        if not full_lines:
            return None
        contributors: dict[str, int] = {}
        for l in full_lines:
            author, _ = l.split("|", 1)
            contributors[author] = contributors.get(author, 0) + 1
        top = sorted(contributors, key=lambda a: -contributors[a])
        last_modified = full_lines[0].split("|", 1)[1]
        return ChangeFrequency(commits_last_90d=0, last_modified=last_modified, top_contributors=top)

    contributors = {}
    for l in lines:
        author, _ = l.split("|", 1)
        contributors[author] = contributors.get(author, 0) + 1
    top = sorted(contributors, key=lambda a: -contributors[a])
    last_modified = lines[0].split("|", 1)[1]
    return ChangeFrequency(commits_last_90d=len(lines), last_modified=last_modified, top_contributors=top)
