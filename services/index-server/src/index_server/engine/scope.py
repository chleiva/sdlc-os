"""Monorepo scope resolution (spec Sec. 6.4): before deep searching,
resolve which package(s) a target path belongs to, rather than treating
the whole monorepo as one flat namespace.

A "package" is the nearest ancestor directory (walking up from the
target path towards the repo root) that carries a recognized package
marker file. This deliberately mirrors how real workspace tooling
(Bazel/Nx/Turborepo-style graphs, or a plain Python/JS package layout)
answers the same question -- see the master spec's own phrasing in
docs/deliverables/wave1-D3-index-server.md's design constraints.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

PACKAGE_MARKERS = (
    "pyproject.toml",
    "setup.py",
    "package.json",
    "PACKAGE.toml",
)


def resolve_package(repo_root: Path, relative_path: str) -> str | None:
    """Return the package-relative-path (posix, relative to repo_root)
    that owns `relative_path`, or None if no ancestor directory carries a
    package marker (the file belongs directly to the repo root).
    """
    p = PurePosixPath(relative_path)
    current = repo_root / p
    current_dir = current.parent if not current.is_dir() else current
    while True:
        try:
            rel = current_dir.relative_to(repo_root)
        except ValueError:
            return None
        for marker in PACKAGE_MARKERS:
            if (current_dir / marker).is_file():
                return "." if str(rel) == "." else rel.as_posix()
        if current_dir == repo_root:
            return None
        current_dir = current_dir.parent


def all_packages(repo_root: Path) -> list[str]:
    """Every package directory in the repo (one level of resolution per
    marker file found), used to give callers the full scope map rather
    than resolving one path at a time.
    """
    found: set[str] = set()
    for marker in PACKAGE_MARKERS:
        for path in repo_root.rglob(marker):
            if ".git" in path.parts or ".venv" in path.parts:
                continue
            rel = path.parent.relative_to(repo_root)
            found.add("." if str(rel) == "." else rel.as_posix())
    return sorted(found)
