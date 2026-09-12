"""Autonomy levels L0-L3 (master spec Sec. 12), configured per
repository/task class -- a config file, never a global constant.

The level -> gating-behavior mapping below is transcribed directly from
Sec. 12's table (see docs pointer in the class docstrings) plus the
narrative paragraph that follows it. It is expressed as small pure
functions rather than one giant if/elif in the gate-orchestration layer
so each level's behavior is independently unit-testable.

ASSUMPTION FLAGGED FOR HUMAN REVIEW (same discipline as
orchestrator/checkpoints.py's DEFAULT_STUCK_RETRY_BUDGET): Sec. 12's
table states, per level, which of {plan-approval, change-review} are
mandatory *gates*; it does not separately restate, for every level,
whether a Sec. 9.3 *checkpoint* (size/risk/time-cost/stuck) still
pauses. This implementation reads Sec. 12.1's own words about L2 --
"a story that trips a Section 9.3 checkpoint is pulled out of its batch
immediately for individual review" -- as the general rule: a Sec. 9.3
checkpoint pauses at every level that runs autonomous implementation at
all (L1, L2, L3), regardless of which approval gates that level makes
mandatory. Checkpoints are a safety net orthogonal to the
approval-gate cadence, not one of the things autonomy trades away. A
human should confirm this reading.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class AutonomyLevel(str, Enum):
    """Sec. 12's four levels, in ascending order of autonomy."""

    L0_ASSISTED = "L0"
    L1_SUPERVISED = "L1"
    L2_DELEGATED = "L2"
    L3_AUTONOMOUS_TO_PR = "L3"


_ORDER = [AutonomyLevel.L0_ASSISTED, AutonomyLevel.L1_SUPERVISED, AutonomyLevel.L2_DELEGATED, AutonomyLevel.L3_AUTONOMOUS_TO_PR]


class UnknownAutonomyLevelError(Exception):
    pass


def parse_level(raw: str) -> AutonomyLevel:
    try:
        return AutonomyLevel(raw)
    except ValueError as exc:
        raise UnknownAutonomyLevelError(f"unknown autonomy level {raw!r}; must be one of {[l.value for l in _ORDER]}") from exc


@dataclass(frozen=True)
class AutonomyConfig:
    """One repository's (optionally: one task-class's) resolved
    autonomy configuration. `plan_approval_sla_business_days` and
    `change_review_sla_business_days` are Sec. 12.1's "two business
    days" default, overridable per repo/task-class exactly like every
    other Sec. 19 threshold."""

    level: AutonomyLevel
    repo: str
    task_class: str | None = None
    plan_approval_sla_business_days: float = 2
    change_review_sla_business_days: float = 2


def requires_plan_approval_gate(level: AutonomyLevel, *, high_risk: bool = False) -> bool:
    """Sec. 12's table, "Mandatory human gates" column, the
    plan-approval half:
      L0 -- always (it is the *only* thing the System does).
      L1 -- always.
      L2 -- only for a story NOT judged routine/low-risk; a routine
            low-risk story instead runs without a plan-approval pause,
            reviewed later as part of its batch (Sec. 12.1).
      L3 -- never (full pipeline runs autonomously through PR).
    """
    if level == AutonomyLevel.L0_ASSISTED:
        return True
    if level == AutonomyLevel.L1_SUPERVISED:
        return True
    if level == AutonomyLevel.L2_DELEGATED:
        return high_risk
    if level == AutonomyLevel.L3_AUTONOMOUS_TO_PR:
        return False
    raise UnknownAutonomyLevelError(repr(level))


def requires_change_review_gate(level: AutonomyLevel, *, pulled_from_batch: bool = False) -> bool:
    """Sec. 12's table, "Mandatory human gates" column, the
    change-review half:
      L0 -- never (there is no autonomous implementation to review).
      L1 -- always, before PR.
      L2 -- not per-story (folded into the batched plan+diff review),
            UNLESS this story was pulled out of its batch for
            individual review (a tripped checkpoint), in which case it
            is reviewed exactly like an L1 story.
      L3 -- never (PR is opened, never gated on a change-review step;
            the separate human/CI merge approval is the only gate,
            and it lives outside this deliverable's authority --
            pr_gate.py).
    """
    if level == AutonomyLevel.L0_ASSISTED:
        return False
    if level == AutonomyLevel.L1_SUPERVISED:
        return True
    if level == AutonomyLevel.L2_DELEGATED:
        return pulled_from_batch
    if level == AutonomyLevel.L3_AUTONOMOUS_TO_PR:
        return False
    raise UnknownAutonomyLevelError(repr(level))


def runs_autonomous_implementation(level: AutonomyLevel) -> bool:
    """L0 is research + plan drafting only; the developer implements
    manually. Every other level runs the System's own implementation
    stage (and is therefore subject to Sec. 9.3 checkpoints)."""
    return level != AutonomyLevel.L0_ASSISTED


def is_batched_review_level(level: AutonomyLevel) -> bool:
    return level == AutonomyLevel.L2_DELEGATED


def never_auto_merges_or_deploys(level: AutonomyLevel) -> bool:
    """Sec. 12's closing paragraph: "No level, including L3, ever
    auto-merges to a protected branch or auto-deploys without a
    separate explicit approval outside the System's own control." This
    is a `True` constant for every level, expressed as a function so
    call sites read as an explicit assertion of the rule rather than a
    magic boolean -- see pr_gate.py and its fitness test for where this
    is enforced structurally, not just asserted here."""
    return True


class AutonomyConfigStore:
    """Per-repository/task-class autonomy configuration, loaded from a
    JSON config file (Sec. 12/19: "configured per repository/task
    class", never a global constant). See config/autonomy.example.json
    for the shape.
    """

    def __init__(self, *, default_level: AutonomyLevel, repositories: dict[str, dict]):
        self._default_level = default_level
        self._repositories = repositories

    @classmethod
    def from_dict(cls, raw: dict) -> "AutonomyConfigStore":
        default_level = parse_level(raw.get("defaults", {}).get("level", AutonomyLevel.L1_SUPERVISED.value))
        return cls(default_level=default_level, repositories=dict(raw.get("repositories", {})))

    @classmethod
    def load_json(cls, path: str | Path) -> "AutonomyConfigStore":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def for_repo(self, repo: str, *, task_class: str | None = None) -> AutonomyConfig:
        repo_cfg = self._repositories.get(repo, {})
        level = repo_cfg.get("level")
        plan_sla = repo_cfg.get("plan_approval_sla_business_days", 2)
        review_sla = repo_cfg.get("change_review_sla_business_days", 2)

        if task_class is not None:
            task_cfg = repo_cfg.get("task_classes", {}).get(task_class)
            if task_cfg is not None:
                level = task_cfg.get("level", level)
                plan_sla = task_cfg.get("plan_approval_sla_business_days", plan_sla)
                review_sla = task_cfg.get("change_review_sla_business_days", review_sla)

        resolved_level = parse_level(level) if level is not None else self._default_level
        return AutonomyConfig(
            level=resolved_level,
            repo=repo,
            task_class=task_class,
            plan_approval_sla_business_days=plan_sla,
            change_review_sla_business_days=review_sla,
        )
