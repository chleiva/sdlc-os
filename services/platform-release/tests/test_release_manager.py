from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from platform_release.audit import AuditLog
from platform_release.canary import CanaryRouter
from platform_release.contract_ci import CIReport, ServiceTestResult
from platform_release.release_manager import ReleaseBlockedError, ReleaseManager
from platform_release.rollback import ReleaseHistory


def _manager(tmp_path: Path, composition_dir: Path) -> ReleaseManager:
    return ReleaseManager(
        composition_dir=str(composition_dir),
        history=ReleaseHistory(base_dir=tmp_path / "history"),
        audit=AuditLog(base_dir=tmp_path / "audit"),
    )


def test_start_release_applies_and_audits(tmp_path, platform_deployment_dir: Path):
    mgr = _manager(tmp_path, platform_deployment_dir)

    outcome = mgr.start_release(environment="platform-demo", platform_version="v1", release_id="rel-1")
    assert outcome.approved

    kinds = [e.kind for e in mgr.audit.all()]
    assert "release_started" in kinds


def test_start_release_blocked_by_failing_ci_report_never_applies(tmp_path, platform_deployment_dir: Path):
    mgr = _manager(tmp_path, platform_deployment_dir)
    failing_report = CIReport(
        results=[ServiceTestResult(service="orchestrator", status="failed", returncode=1, stdout="", stderr="boom")]
    )

    with pytest.raises(ReleaseBlockedError):
        mgr.start_release(
            environment="platform-demo",
            platform_version="v1",
            release_id="rel-blocked",
            ci_report=failing_report,
        )

    # Refused before any tofu apply -- no manifest was ever written.
    assert not (platform_deployment_dir / "deployed-version.json").exists()
    assert mgr.history.current("platform-demo") is None

    kinds = [e.kind for e in mgr.audit.all()]
    assert "release_ci_report" in kinds
    assert "release_started" in kinds  # the attempt itself is still audited


def test_full_release_canary_and_rollback_lifecycle_is_fully_audited(tmp_path, platform_deployment_dir: Path):
    mgr = _manager(tmp_path, platform_deployment_dir)
    passing_report = CIReport(results=[ServiceTestResult(service="orchestrator", status="passed", returncode=0, stdout="", stderr="")])

    first = mgr.start_release(
        environment="platform-demo",
        platform_version="v1",
        release_id="rel-1",
        ci_report=passing_report,
    )
    assert first.approved

    second = mgr.start_release(
        environment="platform-demo",
        platform_version="v2",
        release_id="rel-2",
        ci_report=passing_report,
    )
    assert second.approved

    router = CanaryRouter(old_version="v1", new_version="v2", new_version_fraction=0.1, salt="rel-2")
    sample_ids = [str(uuid.uuid4()) for _ in range(2000)]
    summary = mgr.configure_canary(router, sample_ids, release_id="rel-2")
    assert abs(summary["realized_new_version_fraction"] - 0.1) < 0.02

    mgr.promote_full_rollout(environment="platform-demo", release_id="rel-2", platform_version="v2")

    rollback_outcome = mgr.rollback(environment="platform-demo", release_id="rel-3-rollback")
    assert rollback_outcome.approved
    assert rollback_outcome.manifest["platform_version"] == "v1"
    assert rollback_outcome.manifest["is_rollback"] is True

    kinds = [e.kind for e in mgr.audit.all()]
    for expected in (
        "release_started",
        "release_ci_report",
        "canary_routing_configured",
        "release_promoted_full",
        "rollback_started",
        "rollback_completed",
    ):
        assert expected in kinds, f"missing audit event kind: {expected} (have {kinds})"

    # Both releases and the rollback are three genuinely distinct real
    # `tofu apply` runs sharing one state, driven entirely by this
    # manager -- no manual intervention anywhere in the sequence above.
