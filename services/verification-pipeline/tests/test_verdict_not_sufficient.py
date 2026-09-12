"""Sec. 11.2 acceptance criterion: "A test suite pass alone does not mark
a change eligible for human review without layers 5-7 also completing."
Layers 1-4 all green must still yield an overall "not eligible" verdict
if layer 5, 6, or 7 fails/checkpoints."""
import pytest

from verification_pipeline.layers.base import LayerResult
from verification_pipeline.report import VerificationReport

LAYERS_1_TO_4_ALL_PASS = [
    LayerResult("existing_test_suite", "pass", "ok", {}),
    LayerResult("acceptance_criteria_mapping", "pass", "ok", {}),
    LayerResult("static_analysis", "pass", "ok", {}),
    LayerResult("security_scan", "pass", "ok", {}),
]


def _report_with(layer5_status: str, layer6_status: str, layer7_status: str) -> VerificationReport:
    layers = LAYERS_1_TO_4_ALL_PASS + [
        LayerResult("isolated_context_review", layer5_status, "x", {}),
        LayerResult("behavioral_regression_check", layer6_status, "x", {}),
        LayerResult("cross_codebase_completion_check", layer7_status, "x", {}),
    ]
    return VerificationReport(story_id="SDLC-9", attempt=1, layer_results=layers)


def test_all_seven_layers_green_is_eligible():
    report = _report_with("pass", "pass", "pass")
    assert report.eligible_for_human_review is True


def test_all_seven_layers_green_with_regression_check_skipped_is_still_eligible():
    """Sec. 11.1: the regression check only applies "where applicable" --
    a legitimate skip must not by itself block eligibility."""
    report = _report_with("pass", "skipped", "pass")
    assert report.eligible_for_human_review is True


@pytest.mark.parametrize(
    "layer5,layer6,layer7",
    [
        ("fail", "pass", "pass"),  # isolated review flags a concern
        ("pass", "fail", "pass"),  # behavioral regression detected
        ("pass", "pass", "checkpoint"),  # completion check trips risk checkpoint
    ],
)
def test_layers_1_to_4_green_is_not_sufficient_when_5_6_or_7_fails(layer5, layer6, layer7):
    report = _report_with(layer5, layer6, layer7)

    # The precondition the acceptance criterion cares about: 1-4 are ALL green.
    for name in ("existing_test_suite", "acceptance_criteria_mapping", "static_analysis", "security_scan"):
        assert report.by_name(name).status == "pass"

    assert report.eligible_for_human_review is False
