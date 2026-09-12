from __future__ import annotations

from run_registry import stages

from fleet_dashboard import columns


def test_mapping_is_exhaustive_over_registry_stages():
    """Every non-terminal Registry stage maps to exactly one column; every
    terminal stage maps to none (cleared from the board).
    """
    for stage in stages.NON_TERMINAL_STAGES:
        assert columns.column_for_stage(stage) in columns.COLUMNS, stage
    for stage in stages.TERMINAL_STAGES:
        assert columns.column_for_stage(stage) is None, stage


def test_exact_mapping_from_the_brief():
    expected = {
        stages.INTAKE: columns.ANALYSIS,
        stages.RESEARCH: columns.ANALYSIS,
        stages.PLAN_AUTHORING: columns.DESIGN,
        stages.PLAN_APPROVAL_GATE: columns.DESIGN,
        stages.IMPLEMENTATION: columns.DEVELOPMENT,
        stages.VERIFICATION: columns.TESTING,
        stages.CHANGE_REVIEW_GATE: columns.PR_REVIEW,
        stages.PACKAGING: columns.COMMIT,
        stages.RETROSPECTIVE: columns.COMMIT,
    }
    for stage, column in expected.items():
        assert columns.column_for_stage(stage) == column


def test_unknown_stage_maps_to_no_column():
    assert columns.column_for_stage("not-a-real-stage") is None
