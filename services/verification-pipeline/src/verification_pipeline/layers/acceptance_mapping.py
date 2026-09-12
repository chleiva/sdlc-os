"""Layer 2 -- New/updated tests mapped to acceptance criteria (Sec. 11.1):

"unit tests mapped explicitly to acceptance criteria (each criterion
should trace to at least one test), plus integration tests where the
change crosses a module/service boundary."

The map itself lives on the plan artifact (Sec. 9.5's
acceptance-criteria-to-verification map); this layer is the real
validation that the D7 brief asks for: "every acceptance criterion traces
to at least one test (fail the layer if any criterion has zero mapped
tests)" -- and that the mapped tests actually exist and actually pass, not
just that the map has entries.
"""
from __future__ import annotations

from pathlib import Path

from ..local_pytest import run_pytest
from ..plan_artifact import (
    criteria_missing_tests,
    criteria_requiring_integration_tests,
)
from .base import LayerResult


def run_acceptance_criteria_mapping_layer(
    *,
    plan: dict,
    repo_root: Path,
    integration_test_ids: set[str] | None = None,
    python_executable: str | None = None,
) -> LayerResult:
    integration_test_ids = integration_test_ids or set()

    missing = criteria_missing_tests(plan)
    if missing:
        return LayerResult(
            name="acceptance_criteria_mapping",
            status="fail",
            summary=f"{len(missing)} acceptance criterion/criteria have zero mapped tests: {missing}.",
            details={"missing_criteria": missing},
        )

    # Every criterion has at least one mapped test_id; now actually run
    # them (not just trust the map's word for it) and confirm each mapped
    # test both exists and passes.
    all_test_ids: list[str] = []
    for entry in plan.get("acceptance_criteria_verification_map", []):
        all_test_ids.extend(entry["test_ids"])

    run_result = run_pytest(sorted(set(all_test_ids)), cwd=repo_root, python_executable=python_executable)
    not_passed = [c.node_id for c in run_result.cases if c.outcome != "passed"]
    collected_ids = {c.node_id for c in run_result.cases}
    never_collected = sorted(set(all_test_ids) - collected_ids)

    # Sec. 11.1: integration tests are required where a criterion's change
    # crosses a module/service boundary.
    boundary_criteria = criteria_requiring_integration_tests(plan)
    missing_integration = []
    if boundary_criteria:
        mapped_by_criterion = {
            entry["criterion_id"]: set(entry["test_ids"])
            for entry in plan.get("acceptance_criteria_verification_map", [])
        }
        for cid in boundary_criteria:
            if not (mapped_by_criterion.get(cid, set()) & integration_test_ids):
                missing_integration.append(cid)

    if never_collected or not_passed or missing_integration:
        return LayerResult(
            name="acceptance_criteria_mapping",
            status="fail",
            summary=(
                f"mapped tests not all green: never_collected={never_collected}, "
                f"failed_or_error={not_passed}, missing_integration_test={missing_integration}."
            ),
            details={
                "never_collected": never_collected,
                "not_passed": not_passed,
                "missing_integration": missing_integration,
            },
        )

    return LayerResult(
        name="acceptance_criteria_mapping",
        status="pass",
        summary=f"All {len(plan.get('acceptance_criteria', []))} acceptance criteria trace to at least one passing test.",
        details={"mapped_test_ids": sorted(set(all_test_ids))},
    )
