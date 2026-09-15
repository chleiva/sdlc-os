"""Section 9.3 checkpoint triggers, computed against Section 9.4's default
budgets.

ASSUMPTION FLAGGED FOR HUMAN REVIEW (same discipline as run_registry's
`stages.py`): Section 9.3 names four checkpoint kinds and Section 9.4 pins
concrete size/time/cost numbers, but neither section pins a retry-budget
count for the "stuck" checkpoint ("repeated verification failures on the
same issue beyond a retry budget"). This implementation defaults
`DEFAULT_STUCK_RETRY_BUDGET = 3` consecutive same-stage verification
failures before the stuck checkpoint fires -- a reasonable Phase-0 default
in the same spirit as Section 9.4's own numbers, not verbatim spec text.
A human should confirm this number (or make it per-organization
configuration, consistent with how Section 19 treats the Section 9.4
numbers themselves).
"""

from __future__ import annotations

from dataclasses import dataclass

STORY_SIZES = ("S", "M", "L", "XL")

DEFAULT_STUCK_RETRY_BUDGET = 3


@dataclass(frozen=True)
class Budget:
    story_size: str
    wall_clock_minutes: float
    cost_ceiling_usd: float
    size_checkpoint_lines: int
    size_checkpoint_files: int


# Section 9.4 Phase 0 defaults, verbatim.
DEFAULT_BUDGETS: dict[str, Budget | None] = {
    "S": Budget("S", wall_clock_minutes=30, cost_ceiling_usd=5, size_checkpoint_lines=150, size_checkpoint_files=5),
    "M": Budget("M", wall_clock_minutes=120, cost_ceiling_usd=20, size_checkpoint_lines=400, size_checkpoint_files=15),
    "L": Budget("L", wall_clock_minutes=360, cost_ceiling_usd=60, size_checkpoint_lines=800, size_checkpoint_files=30),
    # XL is deliberately not a runnable budget -- Section 9.4: "not run
    # directly ... the System requests a split rather than implementing an
    # XL story as one run."
    "XL": None,
}

# The 80% pre-warning threshold before the 100% hard stop (Section 9.4).
TIME_COST_WARNING_FRACTION = 0.8

# ASSUMPTION FLAGGED FOR HUMAN REVIEW (a real user confirmed this
# reading on a real live run): Section 9.4's size-checkpoint line
# threshold was calibrated for an incremental change spread across an
# existing codebase, where a large multi-file diff is a real signal
# something may be going further than intended. It fired just as
# eagerly for a single, self-contained new file (a whole game/report/
# script generated in one shot, exactly as asked for) -- a materially
# lower-risk shape (one file to review, no cross-file blast radius)
# than a many-file diff of the same total size. A single-file diff's
# line threshold is multiplied by this factor before the size
# checkpoint's line half of the OR condition is evaluated; the
# file-count half is untouched (a single file can never trip it on
# file count alone anyway).
SINGLE_FILE_LINE_MULTIPLIER = 3


# Real live-run bug this closes: a run's plan authored a story sized "L"
# whose actual implementation produced a 2083-line diff across 2 files --
# nearly 2.6x the "L" budget below -- and only found out from a Section
# 9.3 size checkpoint pausing the run *after* implementation had already
# spent the whole diff. Investigated afterward: no `AgentBackend`
# anywhere in this package ever told the model what a story-size choice
# actually costs -- `story_size`/`cross_cutting_or_high_risk` were free
# text/boolean fields the model filled in with zero visibility into the
# budget its own answer would be held to, so it had no way to plan
# proportionately or ask for decomposition instead. This function is the
# single, generated-from-`DEFAULT_BUDGETS` rendering of that budget every
# vendor backend's plan prompt/tool-description splices in (see
# `tool_use_bedrock_backend.py`'s module docstring for why a plan-
# authoring fix always lands in `bedrock_backend`, `anthropic_backend`,
# `openai_backend`, and `ollama_backend` at once, in lockstep, the same
# discipline already used for the "no test-running subtask" and
# "scope_in must be real paths" fixes) -- generated, not hand-copied
# into four files, so it can never silently drift from the numbers
# `check_size` actually enforces.
def size_budget_prompt_text() -> str:
    lines = [
        "Each story size has a hard diff-size ceiling, checked once implementation finishes "
        "each subtask -- exceeding it pauses the whole run at a size checkpoint until a human "
        "says continue or stop, wasting everything already implemented in the meantime:"
    ]
    for size in STORY_SIZES:
        budget = DEFAULT_BUDGETS.get(size)
        if budget is None:
            lines.append(
                f"- {size}: not a runnable budget at all -- the System never runs an {size} story "
                "directly. If the work is genuinely this large, say so in open_questions and keep "
                "this plan's own story_size at the largest size that actually fits its real scope; "
                "do not pick a smaller size than the work honestly needs just to avoid this."
            )
            continue
        single_file_lines = budget.size_checkpoint_lines * SINGLE_FILE_LINE_MULTIPLIER
        lines.append(
            f"- {size}: at most {budget.size_checkpoint_lines} total changed lines across at most "
            f"{budget.size_checkpoint_files} files (a diff touching only one file gets a wider "
            f"allowance, up to {single_file_lines} lines, since one file to review is materially "
            "lower-risk than the same total size spread across many)."
        )
    lines.append(
        "Pick the smallest size that HONESTLY fits the whole story's real scope, across every "
        "subtask combined -- not the size that merely sounds reasonable. Set "
        "cross_cutting_or_high_risk=true only when the change genuinely is cross-cutting or "
        "high-risk (it bumps the resolved budget one size class automatically); never set it "
        "just to buy a bigger budget for an otherwise-ordinary story. If the real scope will not "
        "fit even inside L's ceiling above, do not silently under-declare story_size to dodge a "
        "checkpoint -- say so plainly in open_questions so a human can split the story into "
        "several smaller ones instead, since a story that size is not meant to run as one plan."
    )
    return "\n".join(lines)


class XLNotRunnableError(Exception):
    """Raised when budget resolution would require an XL budget -- Section
    9.4 treats XL as a decomposition signal, never an executable budget."""


def next_size_class(story_size: str) -> str:
    if story_size not in STORY_SIZES:
        raise ValueError(f"unknown story size {story_size!r}; must be one of {STORY_SIZES}")
    idx = STORY_SIZES.index(story_size)
    if idx + 1 >= len(STORY_SIZES):
        return story_size
    return STORY_SIZES[idx + 1]


def resolve_budget(
    *, story_size: str, cross_cutting_or_high_risk: bool, budgets: dict[str, Budget | None] = DEFAULT_BUDGETS
) -> Budget:
    """Section 9.4: "A story flagged cross-cutting or high-risk is
    budgeted at the next size class up automatically." Raises
    `XLNotRunnableError` if that bump (or the story's own size) resolves
    to XL -- the caller must request a decomposition instead."""
    size = story_size
    if cross_cutting_or_high_risk:
        size = next_size_class(story_size)
    budget = budgets.get(size)
    if budget is None:
        raise XLNotRunnableError(
            f"story size resolves to XL ({story_size!r}, cross_cutting_or_high_risk="
            f"{cross_cutting_or_high_risk}) -- XL is a decomposition signal, not an executable budget"
        )
    return budget


@dataclass(frozen=True)
class DiffStats:
    files_touched: tuple[str, ...]
    lines_changed: int


@dataclass(frozen=True)
class CheckpointTrigger:
    kind: str  # "size" | "risk" | "time_cost" | "stuck"
    reason: str
    details: dict

    def signature(self) -> str:
        """A deterministic fingerprint of this exact trigger occurrence
        (kind + its numeric/file details), used to recognize "the human
        already answered this precise violation" so resolving a
        checkpoint with 'continue' doesn't re-pause on the very next step
        for the identical, already-acknowledged condition. A genuinely
        new violation (more files, a higher number) gets a different
        signature and can still pause again."""
        import json as _json

        return f"{self.kind}:{_json.dumps(self.details, sort_keys=True, default=str)}"


def check_size(diff_stats: DiffStats, budget: Budget) -> CheckpointTrigger | None:
    # See SINGLE_FILE_LINE_MULTIPLIER's own module-level comment for why
    # a single-file diff gets a real, explicit, wider line allowance
    # instead of the bare per-size threshold.
    effective_line_threshold = budget.size_checkpoint_lines
    if len(diff_stats.files_touched) <= 1:
        effective_line_threshold = budget.size_checkpoint_lines * SINGLE_FILE_LINE_MULTIPLIER

    if diff_stats.lines_changed > effective_line_threshold or len(diff_stats.files_touched) > budget.size_checkpoint_files:
        return CheckpointTrigger(
            kind="size",
            reason=(
                f"diff of {diff_stats.lines_changed} changed lines across "
                f"{len(diff_stats.files_touched)} files exceeds the {budget.story_size} "
                f"budget ({effective_line_threshold} lines / {budget.size_checkpoint_files} files)"
            ),
            details={
                "lines_changed": diff_stats.lines_changed,
                "files_touched": len(diff_stats.files_touched),
                "threshold_lines": effective_line_threshold,
                "threshold_files": budget.size_checkpoint_files,
            },
        )
    return None


def check_risk(diff_stats: DiffStats, declared_scope_in: tuple[str, ...]) -> CheckpointTrigger | None:
    """Section 9.3's risk checkpoint, computed exactly as Section 9.5
    requires: a mechanical set-difference between the files the
    in-progress diff actually touches and the plan artifact's declared
    scope -- never a judgment call left to the implementing agent."""
    declared = set(declared_scope_in)
    out_of_scope_touches = sorted(set(diff_stats.files_touched) - declared)
    if out_of_scope_touches:
        return CheckpointTrigger(
            kind="risk",
            reason=f"diff touches file(s) outside the plan's declared scope: {out_of_scope_touches}",
            details={"out_of_scope_files": out_of_scope_touches},
        )
    return None


def check_time_cost(*, elapsed_minutes: float, spend_usd: float, budget: Budget) -> CheckpointTrigger | None:
    warn_minutes = budget.wall_clock_minutes * TIME_COST_WARNING_FRACTION
    warn_cost = budget.cost_ceiling_usd * TIME_COST_WARNING_FRACTION
    if elapsed_minutes >= warn_minutes or spend_usd >= warn_cost:
        return CheckpointTrigger(
            kind="time_cost",
            reason=(
                f"elapsed {elapsed_minutes:.1f}min / ${spend_usd:.2f} spend crossed the 80% "
                f"warning threshold of the {budget.story_size} budget "
                f"({budget.wall_clock_minutes}min / ${budget.cost_ceiling_usd})"
            ),
            details={
                "elapsed_minutes": elapsed_minutes,
                "spend_usd": spend_usd,
                "budget_minutes": budget.wall_clock_minutes,
                "budget_cost_usd": budget.cost_ceiling_usd,
                "hard_stop": elapsed_minutes >= budget.wall_clock_minutes or spend_usd >= budget.cost_ceiling_usd,
            },
        )
    return None


def check_stuck(
    *, consecutive_same_stage_failures: int, retry_budget: int = DEFAULT_STUCK_RETRY_BUDGET
) -> CheckpointTrigger | None:
    if consecutive_same_stage_failures >= retry_budget:
        return CheckpointTrigger(
            kind="stuck",
            reason=(
                f"{consecutive_same_stage_failures} consecutive verification failures on the "
                f"same issue exceeds the retry budget of {retry_budget}"
            ),
            details={"consecutive_failures": consecutive_same_stage_failures, "retry_budget": retry_budget},
        )
    return None


def evaluate_checkpoints(
    *,
    diff_stats: DiffStats,
    declared_scope_in: tuple[str, ...],
    budget: Budget,
    elapsed_minutes: float,
    spend_usd: float,
    consecutive_same_stage_failures: int = 0,
    retry_budget: int = DEFAULT_STUCK_RETRY_BUDGET,
) -> list[CheckpointTrigger]:
    """Evaluate every Section 9.3 checkpoint kind; returns every kind that
    fired (order: size, risk, time_cost, stuck), not just the first, so a
    caller can present/log all simultaneously-true reasons for a pause."""
    triggers = [
        check_size(diff_stats, budget),
        check_risk(diff_stats, declared_scope_in),
        check_time_cost(elapsed_minutes=elapsed_minutes, spend_usd=spend_usd, budget=budget),
        check_stuck(consecutive_same_stage_failures=consecutive_same_stage_failures, retry_budget=retry_budget),
    ]
    return [t for t in triggers if t is not None]
