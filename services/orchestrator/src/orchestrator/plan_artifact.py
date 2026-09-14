"""Section 9.5's structured plan artifact: generator + validator.

The one and only way to produce a plan artifact is `generate_plan_artifact`
-- there is deliberately no "patch"/"update" function in this module. A
later re-plan (Section 9.2) calls `generate_plan_artifact` again with the
model's new `PlanOutput` and a bumped `plan_version`; it is a brand new
artifact, not a mutation of the old one, which is exactly what Section
9.5 means by "regenerated, not hand-edited". `PlanArtifactStore` (below)
keeps every version, so a re-plan is a visible, logged change, never a
silent drift.

ASSUMPTION FLAGGED FOR HUMAN REVIEW: Section 9.5 says the structured plan
artifact is "stored alongside the human-readable plan document" but
neither Section 9.5 nor F2's Registry schema (`run_registry` -- see
`models.py`/the migration SQL) names a field for it; `Run` has no
`plan_artifact_pointer` column. This implementation stores the artifact
as its own durable JSON file per (run_id, plan_version) under a directory
the orchestrator owns (`PlanArtifactStore`), independent of the Registry
process's in-memory state, and does not touch F2's schema. A production
deployment would likely put this in the org's object storage or a
dedicated Registry-adjacent table; a human should confirm this design
rather than this implementation's own choice, before F2 is asked to add a
field for it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from orchestrator.checkpoints import Budget
from orchestrator.model_backend import PlanOutput

SCHEMA_PATH = Path(__file__).parent / "schema" / "plan_artifact.schema.json"
SCHEMA: dict[str, Any] = json.loads(SCHEMA_PATH.read_text())
_VALIDATOR = Draft202012Validator(SCHEMA)


class PlanArtifactValidationError(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def hash_human_plan(human_plan_text: str) -> str:
    return hashlib.sha256(human_plan_text.encode("utf-8")).hexdigest()


def generate_plan_artifact(
    *,
    run_id: str,
    plan_version: int,
    plan_output: PlanOutput,
    budget: Budget,
    human_plan_text: str,
    on_parallel_group_demoted: Callable[[list[str]], None] | None = None,
) -> dict[str, Any]:
    """Build the structured plan artifact from a model-produced
    `PlanOutput`, and validate it against `schema/plan_artifact.schema.json`
    before returning it -- an artifact that doesn't validate is a bug in
    this generator, never shipped to a caller.

    **Real live-run bug this closes**: a model-authored `parallel_group`
    whose subtasks don't all declare an `interface_contract` used to
    raise `PlanArtifactValidationError` right here and abort the entire
    run before it ever reached a human at the plan-approval gate --
    confirmed on a real live run (MiniMax M2.5 set `parallel_group` on
    two subtasks, `interface_contract` on neither). Section 8.1's
    "contracts before parallel writes" is a real safety invariant
    worth keeping, but sequential execution of the same subtasks is
    always safe -- just not concurrent -- so an ungoverned group is now
    repaired (demoted to sequential) rather than treated as fatal; see
    `_demote_ungoverned_parallel_groups`. `on_parallel_group_demoted`,
    if given, is called with the demoted task_ids so a caller (`core.py`)
    can surface this as a real alert instead of a silent behavior
    change."""
    artifact: dict[str, Any] = {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "plan_version": plan_version,
        "generated_at": _now_iso(),
        "source_plan_hash": hash_human_plan(human_plan_text),
        "declared_scope": {
            "in_scope": list(plan_output.scope_in),
            "out_of_scope": list(plan_output.scope_out),
        },
        "acceptance_criteria_map": [
            {
                "criterion_id": ac.criterion_id,
                "description": ac.description,
                "verification_tests": list(ac.verification_tests),
            }
            for ac in plan_output.acceptance_criteria
        ],
        "subtask_graph": {
            "subtasks": [
                {
                    "task_id": st.task_id,
                    "description": st.description,
                    "mode": "parallel" if st.parallel_group else "sequential",
                    "parallel_group": st.parallel_group,
                    "depends_on": list(st.depends_on),
                    "interface_contract": st.interface_contract,
                }
                for st in plan_output.subtasks
            ]
        },
        "risk": {
            "tier": plan_output.risk_tier,
            "cross_cutting_or_high_risk": plan_output.cross_cutting_or_high_risk,
            "story_size": plan_output.story_size,
            "budget": {
                "wall_clock_minutes": budget.wall_clock_minutes,
                "cost_ceiling_usd": budget.cost_ceiling_usd,
                "size_checkpoint_lines": budget.size_checkpoint_lines,
                "size_checkpoint_files": budget.size_checkpoint_files,
            },
        },
    }
    errors = sorted(_VALIDATOR.iter_errors(artifact), key=str)
    if errors:
        raise PlanArtifactValidationError(
            "generated plan artifact failed its own schema: " + "; ".join(e.message for e in errors)
        )
    demoted = _demote_ungoverned_parallel_groups(artifact)
    if demoted:
        # Real, visible signal -- same stdout channel a real live run's
        # driver (`deploy/run-worker/jira_poll_run.py`) already logs
        # through, so this shows up exactly where the fatal crash it
        # replaces used to.
        print(
            f"[plan_artifact] run {run_id!r} plan v{plan_version}: demoted "
            f"parallel subtask(s) {demoted} to sequential execution -- their "
            f"parallel_group had no interface_contract fixed for every "
            f"member (Sec. 8.1 'contracts before parallel writes')."
        )
        if on_parallel_group_demoted is not None:
            on_parallel_group_demoted(demoted)
    return artifact


def _demote_ungoverned_parallel_groups(artifact: dict[str, Any]) -> list[str]:
    """Repairs `artifact` in place: any `parallel_group` with 2+ members
    where at least one lacks a non-null `interface_contract` has every
    member in that group demoted to `parallel_group: null` /
    `mode: "sequential"` -- partial governance (some members contracted,
    some not) isn't real governance, so the whole group loses its
    parallel eligibility, not just the offending member(s). Returns the
    demoted task_ids (empty if every group was already properly
    governed, or there were no parallel groups at all)."""
    subtasks = artifact["subtask_graph"]["subtasks"]
    groups: dict[str, list[dict]] = {}
    for st in subtasks:
        if st["parallel_group"]:
            groups.setdefault(st["parallel_group"], []).append(st)
    demoted: list[str] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        if any(not m["interface_contract"] for m in members):
            for m in members:
                m["parallel_group"] = None
                m["mode"] = "sequential"
                demoted.append(m["task_id"])
    return demoted


def diff_touches_out_of_scope(artifact: dict[str, Any], files_touched: list[str]) -> list[str]:
    """The literal mechanical set-difference Section 9.5 specifies: files
    the in-progress diff touches that are not in the artifact's declared
    in-scope list. Used by `checkpoints.check_risk` via `core.py`."""
    declared = set(artifact["declared_scope"]["in_scope"])
    return sorted(set(files_touched) - declared)


class PlanArtifactStore:
    """Durable, append-only-by-version storage for plan artifacts, keyed
    by run_id. Plain JSON files on disk -- deliberately boring so it
    survives a process restart the same way the Registry's own SQLite
    file does (see the module docstring's flagged assumption on why this
    doesn't live in F2's schema)."""

    def __init__(self, base_dir: Path | str) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _run_dir(self, run_id: str) -> Path:
        d = self._base_dir / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self, artifact: dict[str, Any]) -> None:
        run_dir = self._run_dir(artifact["run_id"])
        path = run_dir / f"v{artifact['plan_version']}.json"
        path.write_text(json.dumps(artifact, indent=2))
        (run_dir / "latest.json").write_text(json.dumps(artifact, indent=2))

    def load_latest(self, run_id: str) -> dict[str, Any] | None:
        path = self._run_dir(run_id) / "latest.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def load_version(self, run_id: str, plan_version: int) -> dict[str, Any] | None:
        path = self._run_dir(run_id) / f"v{plan_version}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def latest_version_number(self, run_id: str) -> int:
        latest = self.load_latest(run_id)
        return latest["plan_version"] if latest else 0
