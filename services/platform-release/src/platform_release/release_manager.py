"""Top-level release orchestration -- the one object that ties ordinary
CI (`contract_ci`), canary routing (`canary`), rollback (`rollback`), and
audit logging (`audit`) together into the actual release/rollback
actions spec §14.15 describes, so every one of those actions produces
its own audit event without each call site having to remember to log it
itself.

This module contains no new *mechanics* -- it composes the other modules
in this package. Read those modules' own docstrings for the actual
"why"; this one is just the glue.
"""

from __future__ import annotations

from dataclasses import dataclass

from platform_release.audit import AuditLog
from platform_release.canary import CanaryRouter, measure_split
from platform_release.contract_ci import CIReport
from platform_release.rollback import ApplyOutcome, ReleaseHistory, release, rollback_to_previous


class ReleaseBlockedError(RuntimeError):
    """Raised when a release is attempted with a CI report that did not
    pass -- ordinary CI (spec §14.15) is a precondition for a release,
    not an advisory."""


@dataclass
class ReleaseManager:
    composition_dir: str
    history: ReleaseHistory
    audit: AuditLog
    actor: str = "platform-release-bot"
    tofu_bin: str = "tofu"

    def start_release(
        self,
        *,
        environment: str,
        platform_version: str,
        release_id: str,
        component_versions: dict[str, str] | None = None,
        ci_report: CIReport | None = None,
    ) -> ApplyOutcome:
        """A forward platform release. If `ci_report` is supplied and did
        not pass, the release is refused before any `tofu apply` runs at
        all -- ordinary CI is a gate, not just a report card (spec
        §14.15's "ordinary CI ... running its own ... tests" is stated as
        part of how a release happens, not as a separate, ignorable
        step)."""
        self.audit.record(
            kind="release_started",
            actor=self.actor,
            details={
                "environment": environment,
                "platform_version": platform_version,
                "release_id": release_id,
                "component_versions": component_versions or {},
            },
        )
        if ci_report is not None:
            self.audit.record(
                kind="release_ci_report",
                actor=self.actor,
                details={"release_id": release_id, **ci_report.as_dict()},
            )
            if not ci_report.all_passed:
                raise ReleaseBlockedError(
                    f"release {release_id!r} blocked: CI report shows failing services {ci_report.failed!r}"
                )

        outcome = release(
            self.composition_dir,
            self.history,
            environment=environment,
            platform_version=platform_version,
            release_id=release_id,
            component_versions=component_versions,
            tofu_bin=self.tofu_bin,
        )
        return outcome

    def configure_canary(self, router: CanaryRouter, sample_identifiers: list[str], *, release_id: str) -> dict:
        """Measures the realized old/new split over a sample of trigger
        identifiers and audits it -- the "measurably limits blast radius"
        half of canary rollout, recorded so a human reviewing the release
        can see the actual observed split, not just the configured
        target."""
        summary = measure_split(router, sample_identifiers)
        self.audit.record(
            kind="canary_routing_configured",
            actor=self.actor,
            details={
                "release_id": release_id,
                "old_version": router.old_version,
                "new_version": router.new_version,
                **summary,
            },
        )
        return summary

    def promote_full_rollout(self, *, environment: str, release_id: str, platform_version: str) -> None:
        self.audit.record(
            kind="release_promoted_full",
            actor=self.actor,
            details={"environment": environment, "release_id": release_id, "platform_version": platform_version},
        )

    def rollback(self, *, environment: str, release_id: str) -> ApplyOutcome:
        """A platform rollback: a single `tofu apply` of the previous
        pinned release, audited on both ends (attempt, then outcome) --
        same discipline as a model-version rollback (spec §13.5), applied
        to the platform's own control-plane version."""
        self.audit.record(
            kind="rollback_started",
            actor=self.actor,
            details={"environment": environment, "release_id": release_id},
        )
        outcome = rollback_to_previous(
            self.composition_dir,
            self.history,
            environment=environment,
            release_id=release_id,
            tofu_bin=self.tofu_bin,
        )
        self.audit.record(
            kind="rollback_completed",
            actor=self.actor,
            details={
                "environment": environment,
                "release_id": release_id,
                "approved": outcome.approved,
                "manifest": outcome.manifest,
            },
        )
        return outcome
