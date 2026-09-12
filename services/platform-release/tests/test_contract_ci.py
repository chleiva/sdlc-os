from __future__ import annotations

from pathlib import Path

import pytest

from platform_release.contract_ci import discover_services, run_platform_ci, run_service_tests

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "fake_services"
REPO_SERVICES_ROOT = Path(__file__).resolve().parents[3] / "services"


def test_discover_services_finds_only_directories_with_tests():
    specs = discover_services(FIXTURES_ROOT)
    assert {s.name for s in specs} == {"svc_a", "svc_b"}


def test_run_service_tests_reports_pass_and_fail_honestly():
    specs = {s.name: s for s in discover_services(FIXTURES_ROOT)}

    result_a = run_service_tests(specs["svc_a"])
    assert result_a.status == "passed"
    assert result_a.returncode == 0

    result_b = run_service_tests(specs["svc_b"])
    assert result_b.status == "failed"
    assert result_b.returncode != 0
    assert "test_fitness_intentionally_fails" in result_b.stdout


def test_run_platform_ci_aggregates_across_services_without_reimplementing_them():
    report = run_platform_ci(FIXTURES_ROOT)

    assert report.passed == ["svc_a"]
    assert report.failed == ["svc_b"]
    assert report.not_runnable == []
    assert report.all_passed is False

    as_dict = report.as_dict()
    assert as_dict["passed"] == ["svc_a"]
    assert as_dict["failed"] == ["svc_b"]


def test_run_platform_ci_all_passed_when_only_passing_service_selected():
    report = run_platform_ci(FIXTURES_ROOT, only=["svc_a"])
    assert report.all_passed is True
    assert report.passed == ["svc_a"]


def test_service_with_no_venv_is_not_runnable_not_silently_passed(tmp_path):
    uninstalled = tmp_path / "not_installed_service"
    (uninstalled / "tests").mkdir(parents=True)
    (uninstalled / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n")

    report = run_platform_ci(tmp_path, only=["not_installed_service"])
    assert report.not_runnable == ["not_installed_service"]
    # not_runnable is not counted as a pass -- distinguishing "never ran"
    # from "ran and passed" is the whole point of the three-way status.
    assert report.passed == []
    # all_passed only judges services that actually ran.
    assert report.all_passed is True


@pytest.mark.skipif(
    not REPO_SERVICES_ROOT.exists(),
    reason="repo services/ tree not present in this checkout",
)
def test_orchestrates_a_real_already_implemented_services_own_contract_tests():
    """Integration proof against the real repo, not just fixtures: this
    CI runner discovers a real Wave-0/1 service (F3's MCP stub contract
    tests) and shells out to *that service's own* pytest -- it does not
    reimplement a single assertion those contract tests make. Scoped to
    one fast, dependency-light real service (mcp-stubs) rather than the
    whole services/ tree, so this test suite doesn't take minutes to run
    or depend on every other service's own venv being present."""
    report = run_platform_ci(REPO_SERVICES_ROOT, only=["mcp-stubs"])
    assert report.not_runnable == [], "expected services/mcp-stubs/.venv to already be set up in this checkout"
    assert report.passed == ["mcp-stubs"], report.as_dict()
