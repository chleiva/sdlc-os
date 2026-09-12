"""Layer 1 -- Existing test suite (Sec. 11.1, master-spec):

"must pass in full for the affected scope; a pre-existing failure
unrelated to the change is flagged, not papered over or silently
skipped."

Two things are wired together here, deliberately:

  * `CIClient` (ci_client.py) -- a real MCP call to F3's actual `ci` stub
    server, proving D7 orchestrates through the real CI contract
    (trigger-run / get-run-status-result) rather than re-implementing
    test execution itself (the D7 brief's "explicitly not in scope").
    This is aggregate-only (tests.total/passed/failed) per ci.schema.json.

  * `local_pytest.run_pytest` -- a real pytest subprocess against the
    affected scope, which supplies the per-test identity the CI contract's
    aggregate counts don't carry, needed to actually distinguish a named
    pre-existing failure from a new one and to drive flaky-test reruns.
    See local_pytest.py's module docstring for why this exists alongside
    the CI contract call rather than instead of it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..ci_client import CIClient
from ..flaky import rerun_failures_and_classify
from ..local_pytest import run_pytest
from .base import LayerResult


def run_existing_test_suite_layer(
    *,
    repo_root: Path,
    scope_paths: list[str],
    known_preexisting_failures: set[str] | None = None,
    tenant_id: str = "tenant-acme",
    repository_name: str = "acme/app",
    ci_client: CIClient | None = None,
    python_executable: str | None = None,
) -> LayerResult:
    known_preexisting_failures = known_preexisting_failures or set()
    ci_client = ci_client or CIClient()

    # 1. Real MCP round-trip through F3's CI contract (audit trail / the
    #    org-CI-triggering integration point this layer is required to use).
    ci_audit: dict[str, Any] = {}
    try:
        trigger_result = ci_client.trigger_run(
            tenant_id=tenant_id, repository=repository_name, ref="verification-pipeline/d7", scope=scope_paths
        )
        ci_audit["trigger_run"] = trigger_result
        if trigger_result.get("outcome") == "ok":
            run_id = trigger_result["data"]["run_id"]
            status_result = ci_client.get_run_status_result(
                tenant_id=tenant_id, repository=repository_name, run_id=run_id
            )
            ci_audit["get_run_status_result"] = status_result
    except Exception as exc:  # pragma: no cover - defensive; CI contract is best-effort audit trail here
        ci_audit["error"] = str(exc)

    # 2. Real local pytest run for per-test granularity.
    run_result = run_pytest(scope_paths, cwd=repo_root, python_executable=python_executable)

    if not run_result.failing_node_ids:
        return LayerResult(
            name="existing_test_suite",
            status="pass",
            summary=f"{run_result.passed}/{run_result.total} existing tests passed for the affected scope.",
            details={"ci_audit": ci_audit, "total": run_result.total, "passed": run_result.passed},
        )

    # 3. Re-run every failure to separate flaky from genuine (Sec. 11.3).
    flaky_check = rerun_failures_and_classify(
        run_result.failing_node_ids, cwd=repo_root, python_executable=python_executable
    )

    # 4. Split genuine failures into pre-existing-and-unrelated vs new.
    new_failures = sorted(set(flaky_check.genuine_failures) - known_preexisting_failures)
    pre_existing = sorted(set(flaky_check.genuine_failures) & known_preexisting_failures)

    details = {
        "ci_audit": ci_audit,
        "total": run_result.total,
        "passed": run_result.passed,
        "flaky_tests": sorted(flaky_check.flaky),
        "pre_existing_failures": pre_existing,
        "new_failures": new_failures,
    }

    if new_failures:
        return LayerResult(
            name="existing_test_suite",
            status="fail",
            summary=f"{len(new_failures)} test(s) newly failing in the affected scope: {new_failures}.",
            details=details,
        )

    if pre_existing:
        return LayerResult(
            name="existing_test_suite",
            status="flagged-pass",
            summary=(
                f"No new failures; {len(pre_existing)} pre-existing failure(s) unrelated to this change "
                f"flagged (not papered over): {pre_existing}."
            ),
            details=details,
        )

    # Every failure turned out flaky and none were pre-existing/new.
    return LayerResult(
        name="existing_test_suite",
        status="flagged-pass",
        summary=f"{len(flaky_check.flaky)} flaky test(s) detected and reported separately from a regression.",
        details=details,
    )
