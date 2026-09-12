from __future__ import annotations

import json
from pathlib import Path

from platform_release.rollback import (
    NoPriorReleaseError,
    ReleaseHistory,
    apply_platform_version,
    release,
    rollback_to_previous,
)

import pytest


def _read_manifest(composition_dir: Path) -> dict:
    return json.loads((composition_dir / "deployed-version.json").read_text())


def test_single_apply_deploys_a_version_for_real(platform_deployment_dir: Path):
    outcome = apply_platform_version(
        platform_deployment_dir,
        environment="platform-demo",
        platform_version="v1",
        release_id="rel-1",
        component_versions={"orchestrator": "v1", "dispatcher": "v1"},
    )
    assert outcome.approved, outcome.tofu_result.stderr
    assert outcome.manifest is not None
    assert outcome.manifest["platform_version"] == "v1"
    assert outcome.manifest["is_rollback"] is False
    assert _read_manifest(platform_deployment_dir)["platform_version"] == "v1"


def test_rollback_is_a_single_tofu_apply_against_the_prior_pinned_state(tmp_path, platform_deployment_dir: Path):
    """The acceptance criterion, verbatim: 'A platform rollback is a
    single tofu apply against a prior pinned state, completing without
    manual intervention.' This test performs two genuine `tofu apply`
    subprocess runs (forward release v1 -> v2, then one rollback call)
    against the *same* tofu state, and asserts the on-disk manifest
    reflects the rollback with no manual step in between."""
    history = ReleaseHistory(base_dir=tmp_path)

    first = release(
        platform_deployment_dir,
        history,
        environment="platform-demo",
        platform_version="v1",
        release_id="rel-1",
        component_versions={"orchestrator": "v1"},
    )
    assert first.approved, first.tofu_result.stderr
    assert _read_manifest(platform_deployment_dir)["platform_version"] == "v1"

    second = release(
        platform_deployment_dir,
        history,
        environment="platform-demo",
        platform_version="v2",
        release_id="rel-2",
        component_versions={"orchestrator": "v2"},
    )
    assert second.approved, second.tofu_result.stderr
    assert _read_manifest(platform_deployment_dir)["platform_version"] == "v2"

    # One call, no extra flags, no manual state surgery -- this is the
    # entire rollback.
    rollback_outcome = rollback_to_previous(
        platform_deployment_dir,
        history,
        environment="platform-demo",
        release_id="rel-3-rollback",
    )

    assert rollback_outcome.approved, rollback_outcome.tofu_result.stderr
    assert rollback_outcome.tofu_result.args[0] == "apply"  # a real `tofu apply`, not some other verb

    manifest = _read_manifest(platform_deployment_dir)
    assert manifest["platform_version"] == "v1"  # rolled back to the prior pinned version
    assert manifest["is_rollback"] is True
    assert manifest["rolled_back_from"] == "v2"
    assert manifest["component_versions"] == {"orchestrator": "v1"}

    # History now shows the rollback as its own recorded release too.
    current = history.current("platform-demo")
    assert current.platform_version == "v1"
    assert current.rolled_back_from == "v2"


def test_rollback_with_no_prior_release_raises_rather_than_silently_noop(tmp_path, platform_deployment_dir: Path):
    history = ReleaseHistory(base_dir=tmp_path)
    # Only one release ever happened -- nothing to roll back to.
    release(
        platform_deployment_dir,
        history,
        environment="platform-demo",
        platform_version="v1",
        release_id="rel-1",
    )
    with pytest.raises(NoPriorReleaseError):
        rollback_to_previous(platform_deployment_dir, history, environment="platform-demo", release_id="rel-2")


def test_release_history_is_durable_across_reload(tmp_path, platform_deployment_dir: Path):
    history = ReleaseHistory(base_dir=tmp_path)
    release(platform_deployment_dir, history, environment="platform-demo", platform_version="v1", release_id="rel-1")
    release(platform_deployment_dir, history, environment="platform-demo", platform_version="v2", release_id="rel-2")

    reloaded = ReleaseHistory(base_dir=tmp_path)
    assert reloaded.current("platform-demo").platform_version == "v2"
    assert reloaded.previous("platform-demo").platform_version == "v1"
