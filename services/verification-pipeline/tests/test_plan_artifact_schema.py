"""D7's own interim plan-artifact schema (Sec. 9.5) -- see
plan_artifact.py's module docstring for the D2-reconciliation flag."""
import pytest
from jsonschema import Draft202012Validator

from verification_pipeline.plan_artifact import (
    PlanArtifactError,
    criteria_missing_tests,
    criteria_requiring_integration_tests,
    load_plan_artifact_schema,
    validate_plan_artifact,
)


def test_schema_itself_is_valid_2020_12():
    schema = load_plan_artifact_schema()
    Draft202012Validator.check_schema(schema)


def test_valid_plan_passes_validation(valid_plan):
    validate_plan_artifact(valid_plan)  # must not raise


def test_missing_required_field_is_rejected(valid_plan):
    broken = dict(valid_plan)
    del broken["risk"]
    with pytest.raises(PlanArtifactError):
        validate_plan_artifact(broken)


def test_every_criterion_has_a_mapped_test_for_valid_plan(valid_plan):
    assert criteria_missing_tests(valid_plan) == []


def test_criterion_with_zero_mapped_tests_is_detected(plan_missing_criterion_test):
    missing = criteria_missing_tests(plan_missing_criterion_test)
    assert missing == ["AC-2"]


def test_crosses_module_boundary_flag_is_read(valid_plan):
    # valid_plan doesn't set it -- should default to not requiring one.
    assert criteria_requiring_integration_tests(valid_plan) == set()

    plan_with_boundary = dict(valid_plan)
    plan_with_boundary["acceptance_criteria_verification_map"] = [
        {
            "criterion_id": "AC-1",
            "test_ids": ["tests/test_new_feature.py::test_compute_discount_applies_rate"],
            "crosses_module_boundary": True,
        }
    ]
    assert criteria_requiring_integration_tests(plan_with_boundary) == {"AC-1"}
