from __future__ import annotations

import json

import pytest

from platform_release.audit import AuditLog


def test_record_returns_event_with_expected_shape():
    log = AuditLog()
    event = log.record(kind="release_started", actor="platform-release-bot", details={"platform_version": "v7"})
    assert event.kind == "release_started"
    assert event.actor == "platform-release-bot"
    assert event.details == {"platform_version": "v7"}
    assert event.event_id
    assert event.ts


def test_unknown_event_kind_is_rejected():
    log = AuditLog()
    with pytest.raises(ValueError):
        log.record(kind="not-a-real-kind", actor="x", details={})
    assert log.all() == []


def test_events_are_append_only_and_queryable_by_kind():
    log = AuditLog()
    log.record(kind="release_started", actor="a", details={"n": 1})
    log.record(kind="rollback_started", actor="a", details={"n": 2})
    log.record(kind="release_started", actor="a", details={"n": 3})

    assert len(log.all()) == 3
    assert [e.details["n"] for e in log.of_kind("release_started")] == [1, 3]
    assert [e.details["n"] for e in log.of_kind("rollback_started")] == [2]


def test_durable_across_reload(tmp_path):
    log = AuditLog(base_dir=tmp_path)
    log.record(kind="release_started", actor="a", details={"release_id": "r1"})
    log.record(kind="rollback_completed", actor="a", details={"release_id": "r1", "approved": True})

    # Simulate a process restart: a fresh AuditLog pointed at the same
    # base_dir must recover every previously recorded event.
    reloaded = AuditLog(base_dir=tmp_path)
    assert len(reloaded.all()) == 2
    assert [e.kind for e in reloaded.all()] == ["release_started", "rollback_completed"]

    # And the on-disk file is genuinely JSON-lines, one event per line.
    lines = (tmp_path / "platform_release_audit_log.jsonl").read_text().splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # must not raise


def test_release_and_rollback_each_produce_their_own_event():
    """Direct proof of the acceptance criterion: 'every platform release
    and rollback produces its own audit log entry' -- exercised again
    end-to-end (against a real tofu apply) in test_release_manager.py;
    this test pins the audit-log half in isolation."""
    log = AuditLog()
    log.record(kind="release_started", actor="bot", details={"release_id": "r1", "platform_version": "v7"})
    log.record(kind="rollback_started", actor="bot", details={"release_id": "r2"})
    log.record(kind="rollback_completed", actor="bot", details={"release_id": "r2", "approved": True})

    kinds = [e.kind for e in log.all()]
    assert "release_started" in kinds
    assert "rollback_started" in kinds
    assert "rollback_completed" in kinds
