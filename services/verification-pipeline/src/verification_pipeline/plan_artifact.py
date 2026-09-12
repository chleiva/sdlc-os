"""Loader/validator for D7's interim plan-artifact schema.

See schema/plan-artifact.schema.json's own description field for the full
"why this exists and isn't D2's real schema yet" note. In short: D2 (the
orchestrator, which owns/generates the real Sec. 9.5 plan artifact) is
being built concurrently by another agent, so this module defines and
validates against D7's own reading of Sec. 9.5's prose rather than
importing anything from D2. Every place this matters is flagged again in
services/verification-pipeline/README.md.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "plan-artifact.schema.json"


def load_plan_artifact_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text())


_SCHEMA = load_plan_artifact_schema()
_VALIDATOR = Draft202012Validator(_SCHEMA)


class PlanArtifactError(Exception):
    """Raised when a plan artifact fails schema validation or a semantic check."""


def validate_plan_artifact(plan: dict[str, Any]) -> None:
    """Raise PlanArtifactError with every violation if `plan` doesn't match
    the schema. Schema-valid does not by itself mean acceptance-criteria
    coverage is complete -- see `criteria_missing_tests` for that check,
    which Layer 2 uses to fail the layer per the D7 brief ("fail the layer
    if any criterion has zero mapped tests")."""
    errors = sorted(_VALIDATOR.iter_errors(plan), key=str)
    if errors:
        details = "; ".join(f"{list(e.path)}: {e.message}" for e in errors)
        raise PlanArtifactError(f"plan artifact does not match schema: {details}")


def criteria_missing_tests(plan: dict[str, Any]) -> list[str]:
    """Return the list of acceptance_criteria ids with zero mapped,
    non-empty test_ids in acceptance_criteria_verification_map. Empty list
    means every criterion traces to at least one test."""
    mapped: dict[str, list[str]] = {}
    for entry in plan.get("acceptance_criteria_verification_map", []):
        mapped.setdefault(entry["criterion_id"], []).extend(entry.get("test_ids", []))

    missing = []
    for criterion in plan.get("acceptance_criteria", []):
        cid = criterion["id"]
        if not mapped.get(cid):
            missing.append(cid)
    return missing


def all_mapped_test_ids(plan: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for entry in plan.get("acceptance_criteria_verification_map", []):
        ids.update(entry.get("test_ids", []))
    return ids


def criteria_requiring_integration_tests(plan: dict[str, Any]) -> set[str]:
    return {
        entry["criterion_id"]
        for entry in plan.get("acceptance_criteria_verification_map", [])
        if entry.get("crosses_module_boundary")
    }
