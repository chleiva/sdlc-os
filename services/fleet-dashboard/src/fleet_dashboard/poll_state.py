"""Ephemeral, in-process poll history.

Per CLAUDE.md / the brief: this dashboard "holds no state of its own
beyond ephemeral UI/session state -- it is not a second place stage
transitions are written to." Everything in this module is exactly that
kind of ephemeral state: it is derived entirely from what this process
has itself observed across successive polls of the Registry Service, is
never written back to the Registry, does not survive a process restart,
and is safe to lose at any time (a restarted dashboard just starts
re-learning it from the next poll onward).

It exists to cover two card fields the brief requires that the Registry
Service's current public API (see run_registry.service.RegistryService)
has no direct read for:

  * "checkpoint count" (Development column) -- the Registry only stores
    the single most recent `checkpoint_pointer`, not a count of how many
    checkpoints have been written. We approximate a count by observing
    the pointer *change* across successive polls.

  * "a visible edge back to Development on a verification failure"
    (Testing column note) -- the Registry's public API does not expose
    per-stage history (`repository.list_stage_history` exists at the
    private SQL layer but `RegistryService` never wraps it), so a
    verification -> implementation loop-back is only detectable by
    watching `Run.stage` change across two polls, the same mechanism
    that already drives the live-update requirement.

FLAGGED FOR HUMAN REVIEW: both of these are approximations bounded by
"since this dashboard process started watching this run" -- a run that
was *already* bounced back from Testing (or already had N checkpoints)
before this process's first poll of it will show a fresh/zero count
until observed directly. The clean fix is for F2 to add two thin,
already-implemented-at-the-SQL-layer read ops to `RegistryService`
(`list_stage_history`, and/or a `checkpoint_count` on `Run`) -- a
shared-contract change this deliverable flags rather than makes
unilaterally (CLAUDE.md ground rule 4).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace

from run_registry import stages


@dataclass(frozen=True)
class RunObservation:
    run_id: str
    stage: str
    # Wall-clock time (this process's clock) this run was first observed
    # sitting at `stage`. Exact if we ourselves saw the transition happen
    # (`stage_since_is_exact`); otherwise it is just "the first poll this
    # process happened to see it at this stage," a lower bound, not a
    # true entry time.
    stage_since: float
    stage_since_is_exact: bool
    checkpoint_pointer: str | None
    checkpoint_observed_count: int
    bounced_from_testing: bool
    bounced_at: float | None


class PollHistory:
    """Thread-safe run_id -> RunObservation history, scoped per tenant.

    One instance is shared by every request handler (HTTP requests are
    served on a thread pool); the lock protects the plain dict beneath
    it. This is intentionally simple in-memory state, not a durable
    store -- see module docstring.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (tenant_id, run_id) -> RunObservation
        self._observations: dict[tuple[str, str], RunObservation] = {}

    def observe(self, *, tenant_id: str, run_id: str, stage: str,
                checkpoint_pointer: str | None, now: float) -> RunObservation:
        """Record one poll's observation of a run and return the
        (possibly updated) running observation for it.
        """
        key = (tenant_id, run_id)
        with self._lock:
            prev = self._observations.get(key)
            if prev is None:
                obs = RunObservation(
                    run_id=run_id,
                    stage=stage,
                    stage_since=now,
                    stage_since_is_exact=False,
                    checkpoint_pointer=checkpoint_pointer,
                    checkpoint_observed_count=1 if checkpoint_pointer else 0,
                    bounced_from_testing=False,
                    bounced_at=None,
                )
                self._observations[key] = obs
                return obs

            stage_changed = stage != prev.stage
            checkpoint_changed = (
                checkpoint_pointer is not None
                and checkpoint_pointer != prev.checkpoint_pointer
            )

            bounced_from_testing = prev.bounced_from_testing
            bounced_at = prev.bounced_at
            if stage_changed and prev.stage == stages.VERIFICATION and stage == stages.IMPLEMENTATION:
                # The exact edge the brief requires be made visible: a
                # verification failure looping back to Development on
                # the SAME card (same run_id), not a new one.
                bounced_from_testing = True
                bounced_at = now
            elif stage_changed and stage != stages.IMPLEMENTATION:
                # Clear the badge once the run has moved on from the
                # Development column it bounced back into.
                bounced_from_testing = False
                bounced_at = None

            obs = replace(
                prev,
                stage=stage,
                stage_since=now if stage_changed else prev.stage_since,
                stage_since_is_exact=True if stage_changed else prev.stage_since_is_exact,
                checkpoint_pointer=checkpoint_pointer,
                checkpoint_observed_count=(
                    prev.checkpoint_observed_count + 1
                    if checkpoint_changed
                    else prev.checkpoint_observed_count
                ),
                bounced_from_testing=bounced_from_testing,
                bounced_at=bounced_at,
            )
            self._observations[key] = obs
            return obs

    def prune(self, *, tenant_id: str, active_run_ids: set[str]) -> None:
        """Drop observations for runs no longer returned for this tenant
        at all (e.g. reached a terminal stage and aged out of
        `list_runs`). Safe to skip -- purely a memory-bound, never a
        correctness concern, since a re-appearing run_id just starts a
        fresh observation.
        """
        with self._lock:
            stale = [
                key
                for key in self._observations
                if key[0] == tenant_id and key[1] not in active_run_ids
            ]
            for key in stale:
                del self._observations[key]
