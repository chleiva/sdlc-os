"""Change-review gate's CODEOWNERS requirement (master spec Sec. 12.1):
"at least one reviewer satisfying the repository's own CODEOWNERS rules
for every file the diff touches."

D3's index server already parses CODEOWNERS for real
(`index_server.engine.ownership`, exercised through
`index_server.engine.repo_index.RepoIndex.ownership`, the same function
backing D3's `get-ownership-metadata` MCP tool). This module calls that
real engine directly rather than reimplementing CODEOWNERS matching --
per the brief, "call it (or its schema-shape) rather than reimplementing
CODEOWNERS parsing."

Interpretation flagged for human review: Sec. 12.1's exact words are
"at least one reviewer ... for every file the diff touches", which is
read here the same way GitHub's own branch-protection CODEOWNERS check
reads it: the *set* of recorded reviewers must jointly cover every
touched file (each file needs at least one owner among the reviewers
who acted), not necessarily one single reviewer who personally owns
every file. A human should confirm this is the intended reading versus
requiring one reviewer to individually satisfy all files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from index_server.engine.repo_index import RepoIndex


@dataclass(frozen=True)
class OwnershipRequirement:
    """Which owner handles are entitled to satisfy the change-review
    gate for a specific diff, per touched file. `owners_by_file[path]`
    is empty when the file has no CODEOWNERS entry at all -- Sec. 12.1
    still requires *some* reviewer in that case (no CODEOWNERS entry is
    not the same as "no review needed"), so an empty owners set can
    never be satisfied by anyone and a human must adjust CODEOWNERS or
    the requirement explicitly.
    """

    repo: str
    owners_by_file: dict[str, frozenset[str]]

    @property
    def touched_files(self) -> tuple[str, ...]:
        return tuple(self.owners_by_file)

    def unsatisfied_files(self, reviewer_handles: frozenset[str]) -> tuple[str, ...]:
        return tuple(
            path for path, owners in self.owners_by_file.items()
            if not (owners & reviewer_handles)
        )

    def is_satisfied_by(self, reviewer_handles: frozenset[str]) -> bool:
        return len(self.unsatisfied_files(reviewer_handles)) == 0


class CodeownersResolver:
    """Resolves an `OwnershipRequirement` for a diff by calling D3's
    real `RepoIndex.ownership` engine against the repository's actual
    working tree (or mirror), once per touched file."""

    def __init__(self, repo_root_resolver):
        # repo_root_resolver: Callable[[str], Path] -- maps a
        # "owner/repo"-style repository identifier to its real
        # on-disk root, exactly like D3's own TenantRegistry does for
        # its MCP server (index_server.tenants.TenantRegistry). Left as
        # an injected callable rather than depending on D3's tenant
        # config format, since D9 is not itself tenant-scoped the same
        # way D3 is.
        self._repo_root_resolver = repo_root_resolver

    def requirement_for(self, repo: str, touched_files: Sequence[str]) -> OwnershipRequirement:
        root: Path = self._repo_root_resolver(repo)
        idx = RepoIndex(root, repo)
        idx.build()
        owners_by_file: dict[str, frozenset[str]] = {}
        for path in touched_files:
            result = idx.ownership(path)
            owners_by_file[path] = frozenset(result["owners"]) if result else frozenset()
        return OwnershipRequirement(repo=repo, owners_by_file=owners_by_file)
