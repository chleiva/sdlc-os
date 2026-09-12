from __future__ import annotations

import pytest

from gates.autonomy import (
    AutonomyConfigStore,
    AutonomyLevel,
    UnknownAutonomyLevelError,
    is_batched_review_level,
    never_auto_merges_or_deploys,
    parse_level,
    requires_change_review_gate,
    requires_plan_approval_gate,
    runs_autonomous_implementation,
)


def test_l0_requires_only_plan_approval():
    assert requires_plan_approval_gate(AutonomyLevel.L0_ASSISTED) is True
    assert requires_change_review_gate(AutonomyLevel.L0_ASSISTED) is False
    assert runs_autonomous_implementation(AutonomyLevel.L0_ASSISTED) is False


def test_l1_requires_both_gates_regardless_of_risk():
    assert requires_plan_approval_gate(AutonomyLevel.L1_SUPERVISED, high_risk=False) is True
    assert requires_plan_approval_gate(AutonomyLevel.L1_SUPERVISED, high_risk=True) is True
    assert requires_change_review_gate(AutonomyLevel.L1_SUPERVISED) is True


def test_l2_skips_plan_approval_for_routine_low_risk_stories():
    assert requires_plan_approval_gate(AutonomyLevel.L2_DELEGATED, high_risk=False) is False
    assert requires_plan_approval_gate(AutonomyLevel.L2_DELEGATED, high_risk=True) is True


def test_l2_change_review_only_when_pulled_from_batch():
    assert requires_change_review_gate(AutonomyLevel.L2_DELEGATED, pulled_from_batch=False) is False
    assert requires_change_review_gate(AutonomyLevel.L2_DELEGATED, pulled_from_batch=True) is True


def test_l3_never_pauses_for_either_gate():
    assert requires_plan_approval_gate(AutonomyLevel.L3_AUTONOMOUS_TO_PR, high_risk=True) is False
    assert requires_change_review_gate(AutonomyLevel.L3_AUTONOMOUS_TO_PR, pulled_from_batch=True) is False
    assert runs_autonomous_implementation(AutonomyLevel.L3_AUTONOMOUS_TO_PR) is True


@pytest.mark.parametrize("level", list(AutonomyLevel))
def test_no_level_including_l3_ever_auto_merges_or_deploys(level):
    assert never_auto_merges_or_deploys(level) is True


def test_is_batched_review_level():
    assert is_batched_review_level(AutonomyLevel.L2_DELEGATED) is True
    assert is_batched_review_level(AutonomyLevel.L1_SUPERVISED) is False


def test_parse_level_rejects_unknown():
    with pytest.raises(UnknownAutonomyLevelError):
        parse_level("L9")


def test_config_is_per_repository_and_task_class_not_a_global_constant():
    store = AutonomyConfigStore.from_dict({
        "defaults": {"level": "L1"},
        "repositories": {
            "acme/app": {
                "level": "L2",
                "plan_approval_sla_business_days": 3,
                "task_classes": {
                    "dependency-bump": {"level": "L3"},
                },
            },
        },
    })

    # Unlisted repo falls back to the configured default, not a
    # hardcoded platform constant.
    other = store.for_repo("acme/unlisted-repo")
    assert other.level == AutonomyLevel.L1_SUPERVISED

    # This repo's own configured level overrides the default.
    app_default = store.for_repo("acme/app")
    assert app_default.level == AutonomyLevel.L2_DELEGATED
    assert app_default.plan_approval_sla_business_days == 3

    # A task class under that repo can override the repo's own level.
    app_dep_bump = store.for_repo("acme/app", task_class="dependency-bump")
    assert app_dep_bump.level == AutonomyLevel.L3_AUTONOMOUS_TO_PR
    # ... but only for that task class; another task class under the
    # same repo still gets the repo-level default.
    app_other_task = store.for_repo("acme/app", task_class="feature-work")
    assert app_other_task.level == AutonomyLevel.L2_DELEGATED


def test_config_loads_from_a_real_json_file(tmp_path):
    import json

    path = tmp_path / "autonomy.json"
    path.write_text(json.dumps({
        "defaults": {"level": "L0"},
        "repositories": {"acme/legacy": {"level": "L1"}},
    }))
    store = AutonomyConfigStore.load_json(path)
    assert store.for_repo("acme/legacy").level == AutonomyLevel.L1_SUPERVISED
    assert store.for_repo("acme/anything-else").level == AutonomyLevel.L0_ASSISTED
