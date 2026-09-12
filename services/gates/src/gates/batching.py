"""L2's batched review cadence (master spec Sec. 12.1): "a batch
closes, and a single consolidated review request is sent, at whichever
comes first -- 5 completed stories, or 24 hours since the batch's first
story completed -- and never spans more than one repository ...
A story that trips a Section 9.3 checkpoint is pulled out of its batch
immediately and routed for individual review rather than waiting for
the batch to close."

Thread-safe (a single lock guards all batch state) since Sec. 12.1's
per-repository batches are naturally submitted from concurrent story
completions across different repositories -- see
tests/test_batching.py's concurrency test.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4

MAX_BATCH_STORIES = 5
MAX_BATCH_AGE = timedelta(hours=24)


@dataclass(frozen=True)
class BatchedStory:
    story_id: str
    repo: str
    completed_at: datetime


@dataclass
class Batch:
    batch_id: str
    repo: str
    opened_at: datetime
    stories: list[BatchedStory] = field(default_factory=list)
    closed: bool = False
    close_reason: str | None = None  # "size" | "age"
    closed_at: datetime | None = None


@dataclass(frozen=True)
class SubmissionResult:
    story_id: str
    repo: str
    routed: str  # "batched" | "individual_review"
    reason: str | None = None  # why routed individually (e.g. "checkpoint")
    batch_id: str | None = None
    batch_closed: bool = False


class BatchManager:
    """One open batch per repository at a time (Sec. 12.1: "never spans
    more than one repository"), enforced structurally -- batches are
    keyed by repo in a dict, so a batch can never contain a second
    repo's story; there is no code path that merges two repos' stories
    into one `Batch`."""

    def __init__(self, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self._clock = clock
        self._lock = threading.Lock()
        self._open: dict[str, Batch] = {}
        self._closed: list[Batch] = []

    def submit_story(
        self, *, story_id: str, repo: str, checkpoint_tripped: bool = False, completed_at: datetime | None = None,
    ) -> SubmissionResult:
        now = completed_at or self._clock()

        if checkpoint_tripped:
            # Sec. 12.1: pulled out immediately, never enters a batch at
            # all -- checked before any batch is touched.
            return SubmissionResult(story_id=story_id, repo=repo, routed="individual_review", reason="checkpoint")

        with self._lock:
            batch = self._open.get(repo)
            if batch is None:
                batch = Batch(batch_id=str(uuid4()), repo=repo, opened_at=now)
                self._open[repo] = batch
            batch.stories.append(BatchedStory(story_id=story_id, repo=repo, completed_at=now))

            close_reason = self._close_reason_locked(batch, now)
            closed = False
            if close_reason is not None:
                self._close_locked(repo, close_reason, now)
                closed = True

            return SubmissionResult(
                story_id=story_id, repo=repo, routed="batched", batch_id=batch.batch_id, batch_closed=closed,
            )

    def sweep_age_based_closures(self, now: datetime | None = None) -> list[str]:
        """Closes any open batch that has crossed the 24h age boundary
        even though no new story has arrived to trigger the check --
        Sec. 12.1's "24 hours since the batch's first story completed"
        half of the "whichever comes first" rule must fire on its own,
        not only when the 6th story happens to show up."""
        current = now or self._clock()
        closed_ids: list[str] = []
        with self._lock:
            for repo in list(self._open):
                batch = self._open[repo]
                reason = self._close_reason_locked(batch, current)
                if reason is not None:
                    self._close_locked(repo, reason, current)
                    closed_ids.append(batch.batch_id)
        return closed_ids

    def _close_reason_locked(self, batch: Batch, now: datetime) -> str | None:
        if len(batch.stories) >= MAX_BATCH_STORIES:
            return "size"
        if now - batch.opened_at >= MAX_BATCH_AGE:
            return "age"
        return None

    def _close_locked(self, repo: str, reason: str, now: datetime) -> Batch:
        batch = self._open.pop(repo)
        batch.closed = True
        batch.close_reason = reason
        batch.closed_at = now
        self._closed.append(batch)
        return batch

    def open_batch_for(self, repo: str) -> Batch | None:
        with self._lock:
            return self._open.get(repo)

    def closed_batches(self) -> list[Batch]:
        with self._lock:
            return list(self._closed)
