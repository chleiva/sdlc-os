"""Optimistic concurrency: a write with a stale version number is
rejected, not silently overwritten (spec Section 14.14 / F2 acceptance
criteria)."""

from __future__ import annotations

from run_registry import stages
from run_registry.errors import ErrorCode
from run_registry.result import Outcome

from .conftest import make_run


def test_stale_version_write_is_rejected(service, tenant_a):
    run = make_run(service, tenant_a)
    stale_version = run.version

    # Caller A advances the stage first, bumping the version.
    r1 = service.transition_stage(
        tenant_id=tenant_a,
        run_id=run.id,
        expected_version=stale_version,
        next_stage=stages.RESEARCH,
    )
    assert r1.outcome == Outcome.OK
    assert r1.data.version == stale_version + 1

    # Caller B, who read the run before caller A's write, retries with
    # the now-stale version it originally read.
    r2 = service.transition_stage(
        tenant_id=tenant_a,
        run_id=run.id,
        expected_version=stale_version,
        next_stage=stages.RESEARCH,
    )
    assert r2.outcome == Outcome.ERROR
    assert r2.error.code == ErrorCode.STALE_VERSION

    # The row must reflect only caller A's write -- never silently
    # overwritten or double-applied.
    final = service.get_run(tenant_id=tenant_a, run_id=run.id)
    assert final.data.version == stale_version + 1
    assert final.data.stage == stages.RESEARCH


def test_stale_version_checkpoint_write_is_rejected(service, tenant_a):
    run = make_run(service, tenant_a)

    ok = service.write_checkpoint(
        tenant_id=tenant_a,
        run_id=run.id,
        expected_version=run.version,
        checkpoint_pointer="s3://bucket/ckpt-A",
    )
    assert ok.outcome == Outcome.OK

    stale = service.write_checkpoint(
        tenant_id=tenant_a,
        run_id=run.id,
        expected_version=run.version,  # stale: already bumped above
        checkpoint_pointer="s3://bucket/ckpt-B-should-not-apply",
    )
    assert stale.outcome == Outcome.ERROR
    assert stale.error.code == ErrorCode.STALE_VERSION

    final = service.get_run(tenant_id=tenant_a, run_id=run.id)
    assert final.data.checkpoint_pointer == "s3://bucket/ckpt-A"


def test_correct_version_after_rereading_succeeds(service, tenant_a):
    run = make_run(service, tenant_a)

    r1 = service.transition_stage(
        tenant_id=tenant_a, run_id=run.id, expected_version=run.version, next_stage=stages.RESEARCH
    )
    assert r1.outcome == Outcome.OK

    # Re-read, then retry with the fresh version -- must succeed.
    reread = service.get_run(tenant_id=tenant_a, run_id=run.id)
    r2 = service.transition_stage(
        tenant_id=tenant_a,
        run_id=run.id,
        expected_version=reread.data.version,
        next_stage=stages.PLAN_AUTHORING,
    )
    assert r2.outcome == Outcome.OK
    assert r2.data.stage == stages.PLAN_AUTHORING
