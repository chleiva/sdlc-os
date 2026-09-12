"""L3's absolute floor (master spec Sec. 12): "PR is opened, not merged;
merge/deploy always requires a separate explicit human or CI approval" --
"at every level, with no exception."

This module is the *only* place in this deliverable that talks to
source control at all, and it exposes exactly one operation: open a PR
and stop. There is no method here (or anywhere else in `services/gates/
src/`) that calls a merge or deploy endpoint -- see
`tests/test_l3_floor_fitness.py` for the structural (AST/grep-based)
proof that no code path in this deliverable's source tree can reach one,
and `tests/test_pr_gate.py` for the behavioral proof that L3 really does
open a PR (via D5's real `SourceControlService`, against its mock) and
nothing further.
"""

from __future__ import annotations

from dataclasses import dataclass

from source_control.service import SourceControlService


@dataclass(frozen=True)
class OpenedPullRequest:
    pr_number: int
    url: str
    state: str  # "open" | "draft"


class PullRequestNotOpenedError(Exception):
    pass


class L3PullRequestGate:
    """L3 ("Autonomous-to-PR"): the System's own authority ends the
    instant a PR exists. Merging or deploying it is always a separate,
    explicit human or CI action outside this class's -- and this
    deliverable's -- own authority (Sec. 12's closing paragraph,
    Sec. 17.2: "the System cannot approve its own gates under any
    autonomy level")."""

    def __init__(self, source_control: SourceControlService):
        self._source_control = source_control

    def open_pr_and_stop(
        self, *, tenant_id: str, repository: str, head_branch: str, base_branch: str, title: str, description: str,
        draft: bool = False,
    ) -> OpenedPullRequest:
        result = self._source_control.open_pr(
            tenant_id=tenant_id, repository=repository, head_branch=head_branch, base_branch=base_branch,
            title=title, description=description, draft=draft,
        )
        if result["outcome"] not in ("ok", "empty"):
            raise PullRequestNotOpenedError(f"open_pr did not succeed: {result}")
        if result["outcome"] == "empty":
            # An open PR already existed for this branch pair -- Sec.
            # 12's "PR is opened" is already satisfied by the existing
            # PR; still never anything further than that.
            pr_number = result["existing_pr_number"]
            return OpenedPullRequest(pr_number=pr_number, url="", state="open")
        data = result["data"]
        return OpenedPullRequest(pr_number=data["pr_number"], url=data["url"], state=data["state"])

    # Deliberately no other public method exists on this class: no
    # `merge_pr`, no `close_pr`, no `deploy`. Adding one would be a
    # deliberate, reviewable change to this file, not an accidental
    # capability picked up from a base class or a wildcard import.
