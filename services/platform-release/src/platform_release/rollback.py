"""Platform release apply / rollback (master spec §14.15):

"A platform rollback is a redeploy of the previous pinned container/
module version (Section 14.2's IaC engine makes this a tofu apply
against a prior state, not a manual undo)."

`apply_platform_version` runs one real `tofu apply` of
`infra/compositions/platform-deployment` against a given
`platform_version` -- used both for an ordinary forward release and,
with `rolled_back_from` set, for a rollback (same function either way:
rollback *is* an apply, not a distinct code path). `ReleaseHistory` is
the durable "what was previously applied" record a caller consults to
find the version to roll back *to* -- so `rollback_to_previous` needs
nothing more than "environment" and can determine the prior pinned
version itself, with no manual "figure out what was there before" step.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from platform_release import tofu_runner


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tf_map_literal(value: dict[str, str]) -> str:
    inner = ", ".join(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in value.items())
    return "{" + inner + "}"


@dataclass(frozen=True)
class ApplyOutcome:
    approved: bool
    tofu_result: tofu_runner.TofuResult
    manifest: dict | None
    manifest_path: Path | None


def apply_platform_version(
    composition_dir: str | Path,
    *,
    environment: str,
    platform_version: str,
    release_id: str,
    component_versions: dict[str, str] | None = None,
    rolled_back_from: str | None = None,
    tofu_bin: str = "tofu",
) -> ApplyOutcome:
    """One real `tofu apply` of the platform-deployment composition. Used
    for both an ordinary forward release and a rollback -- a rollback is
    exactly this same call with `platform_version` set to the prior
    pinned version and `rolled_back_from` set to what is being rolled
    back away from, never a distinct "undo" code path."""
    composition_dir = Path(composition_dir)
    var_args = [
        "-var",
        f"environment={environment}",
        "-var",
        f"platform_version={platform_version}",
        "-var",
        f"release_id={release_id}",
    ]
    if component_versions:
        var_args += ["-var", f"component_versions={_tf_map_literal(component_versions)}"]
    if rolled_back_from is not None:
        var_args += ["-var", f"rolled_back_from={rolled_back_from}"]

    # Idempotent: only actually re-initializes providers/backend the
    # first time this composition directory is used (subsequent calls
    # against the same directory/state are fast no-ops), so both the
    # ordinary forward-release path and the rollback path can call this
    # same function without a separate, easy-to-forget "remember to init
    # first" step -- exactly the "no manual intervention" requirement.
    init_result = tofu_runner.init(composition_dir, tofu_bin=tofu_bin)
    if not init_result.ok:
        return ApplyOutcome(approved=False, tofu_result=init_result, manifest=None, manifest_path=None)

    result = tofu_runner.apply(composition_dir, var_args=var_args, tofu_bin=tofu_bin)

    manifest_path = composition_dir / "deployed-version.json"
    manifest = None
    if result.ok and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())

    return ApplyOutcome(
        approved=result.ok,
        tofu_result=result,
        manifest=manifest,
        manifest_path=manifest_path if manifest is not None else None,
    )


@dataclass(frozen=True)
class ReleaseRecord:
    release_id: str
    environment: str
    platform_version: str
    component_versions: dict
    applied_at: str
    rolled_back_from: str | None = None


class ReleaseHistory:
    """Durable, append-only history of every `apply_platform_version` call
    per environment -- what a rollback consults to find "the previous
    pinned version" without a human having to remember or look it up
    manually. JSON-file-backed for the same durability reasons as this
    package's other stores."""

    _FILENAME = "release_history.json"

    def __init__(self, base_dir: str | Path):
        self._lock = threading.Lock()
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._path = self._base / self._FILENAME
        self._by_env: dict[str, list[dict]] = {}
        if self._path.exists():
            self._by_env = json.loads(self._path.read_text())

    def _persist(self) -> None:
        self._path.write_text(json.dumps(self._by_env, indent=2, sort_keys=True))

    def append(self, record: ReleaseRecord) -> None:
        with self._lock:
            self._by_env.setdefault(record.environment, []).append(asdict(record))
            self._persist()

    def history(self, environment: str) -> list[ReleaseRecord]:
        with self._lock:
            return [ReleaseRecord(**r) for r in self._by_env.get(environment, [])]

    def current(self, environment: str) -> ReleaseRecord | None:
        hist = self.history(environment)
        return hist[-1] if hist else None

    def previous(self, environment: str) -> ReleaseRecord | None:
        """The pinned version applied immediately before the current one
        -- what a rollback re-applies. None if there is no prior release
        to roll back to."""
        hist = self.history(environment)
        return hist[-2] if len(hist) >= 2 else None


class NoPriorReleaseError(RuntimeError):
    pass


def release(
    composition_dir: str | Path,
    history: ReleaseHistory,
    *,
    environment: str,
    platform_version: str,
    release_id: str,
    component_versions: dict[str, str] | None = None,
    tofu_bin: str = "tofu",
) -> ApplyOutcome:
    """An ordinary forward release: apply, then (only on success) record
    it into `history` so a later rollback can find it."""
    outcome = apply_platform_version(
        composition_dir,
        environment=environment,
        platform_version=platform_version,
        release_id=release_id,
        component_versions=component_versions,
        tofu_bin=tofu_bin,
    )
    if outcome.approved:
        history.append(
            ReleaseRecord(
                release_id=release_id,
                environment=environment,
                platform_version=platform_version,
                component_versions=component_versions or {},
                applied_at=_now_iso(),
            )
        )
    return outcome


def rollback_to_previous(
    composition_dir: str | Path,
    history: ReleaseHistory,
    *,
    environment: str,
    release_id: str,
    tofu_bin: str = "tofu",
) -> ApplyOutcome:
    """A single `tofu apply` re-applying the previous pinned release,
    against the same composition's existing state -- no manual
    intervention beyond this one call. Raises `NoPriorReleaseError`
    (rather than silently no-op'ing) if `history` has no prior release
    for this environment to roll back to."""
    current = history.current(environment)
    prior = history.previous(environment)
    if prior is None:
        raise NoPriorReleaseError(f"no prior release recorded for environment {environment!r} to roll back to")

    outcome = apply_platform_version(
        composition_dir,
        environment=environment,
        platform_version=prior.platform_version,
        release_id=release_id,
        component_versions=prior.component_versions,
        rolled_back_from=current.platform_version if current else None,
        tofu_bin=tofu_bin,
    )
    if outcome.approved:
        history.append(
            ReleaseRecord(
                release_id=release_id,
                environment=environment,
                platform_version=prior.platform_version,
                component_versions=prior.component_versions,
                applied_at=_now_iso(),
                rolled_back_from=current.platform_version if current else None,
            )
        )
    return outcome
