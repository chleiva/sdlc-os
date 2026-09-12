"""Layer 6 -- Behavioral/regression check: real code executed for real,
compared against a recorded baseline (D7 brief: "where a baseline exists
... detect a regression for real against a fixture")."""
from verification_pipeline.layers.regression_check import run_regression_check_layer

DATA = [1, 2, 3, 5, 4, 6, 7, 9, 8, 10, 11, 12, 14, 13, 15, 16, 17, 19, 18, 20, 21, 22, 24, 23, 25, 26, 27, 29, 28, 30]


def test_no_recorded_baseline_is_skipped_not_papered_over(regression_dir, tmp_path):
    result = run_regression_check_layer(
        baseline_path=tmp_path / "no-such-baseline.json",
        module_path=regression_dir / "good_impl.py",
        attr="measure_comparisons",
        measure_args=(DATA,),
    )
    assert result.status == "skipped"
    assert not result.blocks_human_review


def test_implementation_within_baseline_tolerance_passes(regression_dir):
    result = run_regression_check_layer(
        baseline_path=regression_dir / "baseline.json",
        module_path=regression_dir / "good_impl.py",
        attr="measure_comparisons",
        measure_args=(DATA,),
    )
    assert result.status == "pass"
    assert result.details["current_value"] == result.details["baseline_value"]


def test_real_regression_is_detected_against_the_fixture_baseline(regression_dir):
    result = run_regression_check_layer(
        baseline_path=regression_dir / "baseline.json",
        module_path=regression_dir / "regressed_impl.py",
        attr="measure_comparisons",
        measure_args=(DATA,),
    )
    assert result.status == "fail"
    assert result.blocks_human_review
    assert result.details["current_value"] > result.details["baseline_value"]
