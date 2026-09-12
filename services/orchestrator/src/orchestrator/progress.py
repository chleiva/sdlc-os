"""Durable, per-run implementation progress: which subtasks are done, the
diff accumulated so far, and the checkpoint-relevant counters (spend,
consecutive same-stage failures).

Same flagged-assumption rationale as `plan_artifact.PlanArtifactStore`:
F2's Registry schema has no columns for these (only a single opaque
`checkpoint_pointer` string, reserved here for the pending-elicitation
record -- see `core.py`), so this data lives in its own durable store the
orchestrator owns. It is what makes "a run resumes correctly from its
last Registry checkpoint after a simulated process restart" true at
finer granularity than just the coarse nine-stage vocabulary: a restart
mid-implementation resumes at the next not-yet-completed subtask, not
from the top of the stage.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunProgress:
    run_id: str
    attempt_id: str
    completed_subtask_ids: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    lines_changed: int = 0
    spend_usd: float = 0.0
    consecutive_same_stage_failures: int = 0
    started_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "RunProgress":
        return RunProgress(**d)

    def elapsed_minutes(self, *, now: datetime | None = None) -> float:
        started = datetime.fromisoformat(self.started_at)
        current = now or datetime.now(timezone.utc)
        return (current - started).total_seconds() / 60.0


class RunProgressStore:
    def __init__(self, base_dir: Path | str) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        return self._base_dir / f"{run_id}.json"

    def save(self, progress: RunProgress) -> None:
        self._path(progress.run_id).write_text(json.dumps(progress.to_dict(), indent=2))

    def load(self, run_id: str) -> RunProgress | None:
        path = self._path(run_id)
        if not path.exists():
            return None
        return RunProgress.from_dict(json.loads(path.read_text()))

    def start_new(self, *, run_id: str, attempt_id: str) -> RunProgress:
        progress = RunProgress(run_id=run_id, attempt_id=attempt_id)
        self.save(progress)
        return progress
