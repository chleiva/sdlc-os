"""Layer 2 -- New/updated tests mapped to acceptance criteria. D7 brief:
"real validation that every acceptance criterion traces to at least one
test (fail the layer if any criterion has zero mapped tests)."."""
from verification_pipeline.layers.acceptance_mapping import run_acceptance_criteria_mapping_layer


def test_fully_mapped_plan_with_passing_tests_passes(valid_plan, target_repo):
    result = run_acceptance_criteria_mapping_layer(plan=valid_plan, repo_root=target_repo)
    assert result.status == "pass"
    assert not result.blocks_human_review


def test_criterion_with_zero_mapped_tests_fails_the_layer(plan_missing_criterion_test, target_repo):
    result = run_acceptance_criteria_mapping_layer(plan=plan_missing_criterion_test, repo_root=target_repo)
    assert result.status == "fail"
    assert result.blocks_human_review
    assert result.details["missing_criteria"] == ["AC-2"]


def test_mapped_test_that_does_not_exist_fails_the_layer(valid_plan, target_repo):
    broken_plan = dict(valid_plan)
    broken_plan["acceptance_criteria_verification_map"] = [
        {"criterion_id": "AC-1", "test_ids": ["tests/test_new_feature.py::test_this_does_not_exist"]}
    ]
    result = run_acceptance_criteria_mapping_layer(plan=broken_plan, repo_root=target_repo)
    assert result.status == "fail"
    assert "tests/test_new_feature.py::test_this_does_not_exist" in result.details["never_collected"]


def test_mapped_test_that_fails_fails_the_layer(valid_plan, target_repo):
    broken_plan = dict(valid_plan)
    broken_plan["acceptance_criteria_verification_map"] = [
        {"criterion_id": "AC-1", "test_ids": ["tests/test_preexisting_failure.py::test_known_preexisting_bug"]}
    ]
    result = run_acceptance_criteria_mapping_layer(plan=broken_plan, repo_root=target_repo)
    assert result.status == "fail"
    assert "tests/test_preexisting_failure.py::test_known_preexisting_bug" in result.details["not_passed"]


def test_boundary_crossing_criterion_requires_an_integration_test(valid_plan, target_repo):
    plan = dict(valid_plan)
    plan["acceptance_criteria_verification_map"] = [
        {
            "criterion_id": "AC-1",
            "test_ids": ["tests/test_new_feature.py::test_compute_discount_applies_rate"],
            "crosses_module_boundary": True,
        }
    ]
    result_without_integration = run_acceptance_criteria_mapping_layer(
        plan=plan, repo_root=target_repo, integration_test_ids=set()
    )
    assert result_without_integration.status == "fail"
    assert result_without_integration.details["missing_integration"] == ["AC-1"]

    result_with_integration = run_acceptance_criteria_mapping_layer(
        plan=plan,
        repo_root=target_repo,
        integration_test_ids={"tests/test_new_feature.py::test_compute_discount_applies_rate"},
    )
    assert result_with_integration.status == "pass"
