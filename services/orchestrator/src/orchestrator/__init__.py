"""D2 -- Agent Orchestrator Core (SDLC Auto, Wave 1).

The orchestrator drives one tenant's run through the nine-stage workflow
(master spec Section 5), enforcing the Hook chain on every tool call
(Section 7), generating and checking the structured plan artifact
(Section 9.5), computing checkpoint triggers against the Section 9.4
default budgets, and isolating multi-agent work with a git-worktree per
agent/session (Section 8.1).

Public surface is intentionally small; see `orchestrator.core.Orchestrator`
for the state machine entry point.
"""

from orchestrator.core import Orchestrator, RunStatus

__all__ = ["Orchestrator", "RunStatus"]
