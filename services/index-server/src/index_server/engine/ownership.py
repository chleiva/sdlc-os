"""CODEOWNERS parsing + change-frequency metadata (spec Sec. 6.2),
implementing the get-ownership-metadata tool's real logic.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

CODEOWNERS_CANDIDATES = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")


@dataclass(frozen=True)
class OwnersRule:
    pattern: str
    owners: tuple[str, ...]


def _pattern_matches(pattern: str, path: str) -> bool:
    """GitHub CODEOWNERS-style matching, simplified: a pattern with no
    slash matches the basename anywhere; a pattern with a slash matches
    from the repo root; a trailing '/' or '/*' means "this directory and
    everything under it".
    """
    pattern = pattern.strip()
    if pattern in ("*", "/*"):
        return True
    normalized = pattern.lstrip("/")
    if normalized.endswith("/"):
        return path == normalized.rstrip("/") or path.startswith(normalized)
    if "/" not in pattern:
        return fnmatch.fnmatch(PurePosixPath(path).name, normalized)
    if fnmatch.fnmatch(path, normalized):
        return True
    return path.startswith(normalized.rstrip("*"))


def parse_codeowners(repo_root: Path) -> list[OwnersRule]:
    for candidate in CODEOWNERS_CANDIDATES:
        p = repo_root / candidate
        if p.is_file():
            rules = []
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                pattern, owners = parts[0], tuple(parts[1:])
                if owners:
                    rules.append(OwnersRule(pattern, owners))
            return rules
    return []


def owners_for_path(rules: list[OwnersRule], path: str) -> list[str]:
    """CODEOWNERS semantics: the LAST matching rule wins (more specific
    rules are conventionally placed later in the file)."""
    matched: list[str] = []
    for rule in rules:
        if _pattern_matches(rule.pattern, path):
            matched = list(rule.owners)
    return matched
