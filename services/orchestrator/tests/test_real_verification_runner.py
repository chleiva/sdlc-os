"""Tests for `orchestrator.real_verification_runner.RealVerificationRunner`
-- previously exercised only through manual, uncommitted live-run smoke
testing (see that module's own docstring history of real bugs found
that way). Real pytest/ruff/mypy subprocesses against a real `tmp_path`
workspace, no mocking of the tools themselves.
"""

from __future__ import annotations

from orchestrator.checkpoints import DEFAULT_BUDGETS
from orchestrator.plan_artifact import PlanArtifactStore, generate_plan_artifact
from orchestrator.progress import RunProgress, RunProgressStore
from orchestrator.real_verification_runner import RealVerificationRunner

from ._factories import make_plan_output


class TestPytestTargetPaths:
    """Real bug this closes -- the true root cause behind a real live
    run's repeated "stuck" checkpoints: pytest treats an *explicit*
    positional argument that doesn't match its own `python_files`
    convention as a hard "not found" collection error, not a silent
    skip (unlike ruff/mypy, which tolerate a non-Python path in scope
    just fine). Only `.py` files were ever valid pytest targets; the
    rest of `files_touched` (config files a fix-up subtask wrote, the
    actual HTML/JS deliverable itself) never was."""

    def test_filters_to_python_files_only(self):
        scope = ["conftest.py", "mypy.ini", "pytest.ini", "ruff.toml", "test_tetris.py", "tetris.html"]
        assert RealVerificationRunner._pytest_target_paths(scope) == ["conftest.py", "test_tetris.py"]

    def test_falls_back_to_whole_workspace_scan_if_nothing_is_python(self):
        # A run whose only real deliverable is non-Python (an HTML/JS
        # game, say) must still hand pytest *something* real to look
        # at -- "." (pytest's own real testpaths/discovery), never an
        # empty target list.
        assert RealVerificationRunner._pytest_target_paths(["tetris.html", "styles.css"]) == ["."]

    def test_all_python_scope_is_unchanged(self):
        assert RealVerificationRunner._pytest_target_paths(["a.py", "b.py"]) == ["a.py", "b.py"]


def _seed_run(tmp_path, *, files_touched: list[str]) -> tuple[str, PlanArtifactStore, RunProgressStore]:
    run_id = "run-1"
    plan_store = PlanArtifactStore(tmp_path / "plans")
    progress_store = RunProgressStore(tmp_path / "progress")

    plan_output = make_plan_output(story_size="S")
    artifact = generate_plan_artifact(
        run_id=run_id, plan_version=1, plan_output=plan_output,
        budget=DEFAULT_BUDGETS["S"], human_plan_text="test plan",
    )
    plan_store.save(artifact)

    progress = RunProgress(run_id=run_id, attempt_id="a1", files_touched=files_touched)
    progress_store.save(progress)

    return run_id, plan_store, progress_store


def test_real_run_against_a_non_python_deliverable_does_not_fabricate_a_pytest_failure(tmp_path):
    """Reproduces the exact real live-run scenario: a real Python test
    file alongside real non-Python files (a config file, the actual
    HTML/JS deliverable) all recorded in `files_touched`. Before the
    fix, pytest was handed the *entire* list as explicit targets and
    reported "not found" for every non-.py entry, escalating to a real,
    fabricated verification failure regardless of whether the real
    test itself passed."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "pytest.ini").write_text("[pytest]\npython_files = test_*.py\ntestpaths = .\n")
    (workspace / "test_thing.py").write_text("def test_ok():\n    assert True\n")
    (workspace / "app.html").write_text("<html></html>\n")
    (workspace / "config.ini").write_text("[section]\nkey = value\n")

    run_id, plan_store, progress_store = _seed_run(
        tmp_path, files_touched=["pytest.ini", "test_thing.py", "app.html", "config.ini"],
    )
    runner = RealVerificationRunner(workspace_root=workspace, plan_store=plan_store, progress_store=progress_store)

    result = runner.run(run_context={"run_id": run_id})

    assert "not found" not in result.summary.lower()
    assert result.tests_total == 1
    assert result.tests_failed == 0
