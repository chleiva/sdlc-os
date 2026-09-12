"""AC4: "An L2 batch closes at 5 stories or 24 hours, whichever comes
first, and never mixes two repositories in one batch." All four
conditions tested independently, plus the never-spans-repos invariant
under concurrent stories from two repositories."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from gates.batching import MAX_BATCH_AGE, MAX_BATCH_STORIES, BatchManager

START = datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc)


def test_condition_1_batch_closes_at_5_stories_before_24h_elapses():
    mgr = BatchManager()
    results = []
    for i in range(5):
        results.append(mgr.submit_story(story_id=f"S{i}", repo="acme/app", completed_at=START + timedelta(minutes=i)))

    assert [r.batch_closed for r in results] == [False, False, False, False, True]
    assert results[-1].reason is None
    closed = mgr.closed_batches()
    assert len(closed) == 1
    assert closed[0].close_reason == "size"
    assert len(closed[0].stories) == MAX_BATCH_STORIES
    assert mgr.open_batch_for("acme/app") is None


def test_condition_2_batch_closes_at_24_hours_even_with_fewer_than_5_stories():
    mgr = BatchManager()
    mgr.submit_story(story_id="S0", repo="acme/app", completed_at=START)
    mgr.submit_story(story_id="S1", repo="acme/app", completed_at=START + timedelta(hours=1))

    # No new story arrives, but a periodic sweep still closes it once
    # 24h have passed since the batch's first story.
    still_open = mgr.sweep_age_based_closures(START + timedelta(hours=23))
    assert still_open == []
    assert mgr.open_batch_for("acme/app") is not None

    closed_ids = mgr.sweep_age_based_closures(START + MAX_BATCH_AGE)
    assert len(closed_ids) == 1
    closed = mgr.closed_batches()
    assert closed[0].close_reason == "age"
    assert len(closed[0].stories) == 2


def test_condition_3_batch_stays_open_below_both_thresholds():
    mgr = BatchManager()
    mgr.submit_story(story_id="S0", repo="acme/app", completed_at=START)
    result = mgr.submit_story(story_id="S1", repo="acme/app", completed_at=START + timedelta(hours=1))
    assert result.batch_closed is False
    assert mgr.open_batch_for("acme/app") is not None
    assert mgr.sweep_age_based_closures(START + timedelta(hours=2)) == []


def test_condition_4_checkpoint_tripping_story_is_pulled_out_immediately():
    mgr = BatchManager()
    mgr.submit_story(story_id="S0", repo="acme/app", completed_at=START)
    result = mgr.submit_story(story_id="S1", repo="acme/app", checkpoint_tripped=True, completed_at=START)

    assert result.routed == "individual_review"
    assert result.reason == "checkpoint"
    assert result.batch_id is None
    # The checkpoint-tripped story never entered the batch at all.
    batch = mgr.open_batch_for("acme/app")
    assert len(batch.stories) == 1
    assert batch.stories[0].story_id == "S0"


def test_never_spans_two_repositories():
    mgr = BatchManager()
    mgr.submit_story(story_id="A0", repo="acme/app", completed_at=START)
    mgr.submit_story(story_id="B0", repo="acme/billing", completed_at=START)
    mgr.submit_story(story_id="A1", repo="acme/app", completed_at=START + timedelta(minutes=5))

    app_batch = mgr.open_batch_for("acme/app")
    billing_batch = mgr.open_batch_for("acme/billing")
    assert app_batch.batch_id != billing_batch.batch_id
    assert {s.repo for s in app_batch.stories} == {"acme/app"}
    assert {s.repo for s in billing_batch.stories} == {"acme/billing"}
    assert len(app_batch.stories) == 2
    assert len(billing_batch.stories) == 1


def test_concurrent_stories_from_two_repos_never_mix():
    mgr = BatchManager()
    errors: list[Exception] = []

    def submit(repo: str, count: int):
        try:
            for i in range(count):
                mgr.submit_story(story_id=f"{repo}-{i}", repo=repo, completed_at=START + timedelta(seconds=i))
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)

    threads = [
        threading.Thread(target=submit, args=("acme/app", 4)),
        threading.Thread(target=submit, args=("acme/billing", 4)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    app_batch = mgr.open_batch_for("acme/app")
    billing_batch = mgr.open_batch_for("acme/billing")
    assert {s.repo for s in app_batch.stories} == {"acme/app"}
    assert {s.repo for s in billing_batch.stories} == {"acme/billing"}
    assert len(app_batch.stories) == 4
    assert len(billing_batch.stories) == 4
    # Every closed batch (if either crossed a threshold) is still
    # single-repo -- the invariant that must hold regardless of timing.
    for batch in mgr.closed_batches():
        assert len({s.repo for s in batch.stories}) == 1
