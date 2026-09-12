"""The `AgentBackend` interface -- the one seam in this deliverable where
a real model call plugs in.

**There is no live LLM API endpoint in this environment** (D6, which will
eventually serve the pinned model behind an OpenAI-compatible endpoint,
does not exist as a running service here -- see the D2 brief and D6's own
brief, `docs/deliverables/wave1-D6-model-serving-tenant-cell.md`). Every
other module in this package (the nine-stage state machine, the Hook
chain, the plan-artifact generator, checkpoint-trigger computation,
worktree isolation, structured elicitation) is real, production-shaped
orchestration logic that does not care which `AgentBackend` implementation
it is handed.

**Where a real model plugs in later**: implement `AgentBackend` with a
class whose `author_plan` / `re_plan` / `implement_subtask` methods call
D6's future OpenAI-compatible chat-completions endpoint (model-agnostic
per spec Section 8.5 -- a "smart friend" escalation path, or a distinct
verifier model per Section 8.4/13.4, are just different `AgentBackend` --
or `VerificationRunner`, see `verification.py` -- instances passed to the
same `Orchestrator`). Nothing in `core.py` imports an HTTP client or any
model-specific SDK; it only calls the methods defined on this abstract
base class.

For this pass, `ScriptedAgentBackend` is the deterministic mock used by
every test in this package: it returns canned plan/diff outputs from a
fixed, caller-supplied script (a list of `PlanOutput`/`DiffOutput` values
consumed in order), so tests can drive the state machine through all nine
stages, through checkpoint triggers, and through re-plan/retry loops
without any network call or model weights -- the same "real code, mocked
external boundary" discipline every other deliverable in this system
(F3's stub MCP servers, D5's mock GitHub server) already uses.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class AcceptanceCriterion:
    criterion_id: str
    description: str
    verification_tests: tuple[str, ...]  # test names/paths that demonstrate it


@dataclass(frozen=True)
class SubTask:
    task_id: str
    description: str
    parallel_group: str | None  # subtasks sharing a group may run concurrently
    depends_on: tuple[str, ...] = ()
    interface_contract: str | None = None  # fixed request/response shape, §8.1 "contracts before parallel writes"


@dataclass(frozen=True)
class PlanOutput:
    """One model-produced plan (Section 9.1's six questions), the input
    `plan_artifact.generate_plan_artifact` turns into the structured
    contract (Section 9.5)."""

    outcomes: str
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    scope_in: tuple[str, ...]  # files/modules the plan expects to touch
    scope_out: tuple[str, ...]  # explicitly out of scope
    subtasks: tuple[SubTask, ...]
    story_size: str  # one of "S", "M", "L", "XL" -- §4.3/§9.4
    cross_cutting_or_high_risk: bool
    risk_tier: str  # "low" | "medium" | "high" -- §12's classification, held generically here
    rollback_strategy: str
    constraints: str = ""
    prior_decisions: str = ""
    open_questions: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiffOutput:
    """One implementation step's produced diff, in exactly the shape the
    Section 9.3 risk checkpoint needs to mechanically diff against the
    plan artifact's declared scope: the literal set of files touched."""

    files_touched: tuple[str, ...]
    lines_changed: int
    commit_message: str
    subtask_id: str | None = None


class AgentBackend(ABC):
    """What a Skill calls to get model output. Swap the implementation,
    never the call sites in `core.py`."""

    @abstractmethod
    def author_plan(self, *, run_context: dict) -> PlanOutput: ...

    @abstractmethod
    def re_plan(self, *, run_context: dict, feedback: str) -> PlanOutput:
        """Section 9.2 "request changes": revise and re-submit -- never
        starts implementing the plan that was not the one approved."""
        ...

    @abstractmethod
    def implement_subtask(self, *, run_context: dict, subtask: SubTask) -> DiffOutput: ...


class ScriptedAgentBackend(AgentBackend):
    """Deterministic mock `AgentBackend`: canned outputs consumed in a
    fixed order from a caller-supplied script. Never calls a network or a
    model -- see module docstring."""

    def __init__(
        self,
        *,
        plans: list[PlanOutput] | None = None,
        diffs: list[DiffOutput] | None = None,
    ) -> None:
        self._plans = list(plans or [])
        self._diffs = list(diffs or [])
        self.plan_call_count = 0
        self.implement_call_count = 0

    def author_plan(self, *, run_context: dict) -> PlanOutput:
        return self._next_plan()

    def re_plan(self, *, run_context: dict, feedback: str) -> PlanOutput:
        return self._next_plan()

    def _next_plan(self) -> PlanOutput:
        if not self._plans:
            raise RuntimeError("ScriptedAgentBackend: no more scripted plans")
        self.plan_call_count += 1
        return self._plans.pop(0)

    def implement_subtask(self, *, run_context: dict, subtask: SubTask) -> DiffOutput:
        if not self._diffs:
            raise RuntimeError("ScriptedAgentBackend: no more scripted diffs")
        self.implement_call_count += 1
        return self._diffs.pop(0)
