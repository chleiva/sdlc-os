"""D9 -- Gates: Plan-Approval/Change-Review Flow, Autonomy Levels,
Escalation (master spec Sec. 12, 12.1, 9.2, 9.3, 17.2).

This package is the human-in-the-loop machinery layered on top of D2's
raw gate/checkpoint mechanics (`Orchestrator.approve_plan`,
`approve_change_review`, `resolve_checkpoint`): it resolves *which*
human is authorized to act on a given gate, enforces that the identity
which triggered a run can never by itself satisfy a gate it triggered,
enforces the CODEOWNERS reviewer requirement for change-review,
escalates an unactioned gate at its SLA boundary, batches L2's routine
low-risk stories, and is L3's structural floor against ever producing a
merge/deploy action.

See README.md for the module-by-module layout and the final agent
report for the acceptance-criteria -> test-file mapping and what's
mocked vs. real.
"""

from __future__ import annotations
