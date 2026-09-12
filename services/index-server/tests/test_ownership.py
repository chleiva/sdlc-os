"""get-ownership-metadata: CODEOWNERS parsing + real git-log-derived
change-frequency (spec Sec. 6.2)."""
from index_server.engine import git_utils
from index_server.engine.repo_index import RepoIndex


def test_codeowners_last_match_wins_for_specific_file(static_fixture_repo):
    idx = RepoIndex(static_fixture_repo, "fixture/multi-pkg-repo")
    idx.build()
    result = idx.ownership("pkg_a/billing/util.py")
    assert result is not None
    assert set(result["owners"]) == {"@billing-team", "@jane-doe"}


def test_codeowners_directory_rule_applies_to_other_files(static_fixture_repo):
    idx = RepoIndex(static_fixture_repo, "fixture/multi-pkg-repo")
    idx.build()
    result = idx.ownership("pkg_a/billing/service.py")
    assert result["owners"] == ["@billing-team"]
    result_b = idx.ownership("pkg_b/reporting/report.py")
    assert result_b["owners"] == ["@reporting-team"]


def test_no_codeowners_match_is_reported_as_no_signal(static_fixture_repo):
    idx = RepoIndex(static_fixture_repo, "fixture/multi-pkg-repo")
    idx.build()
    assert idx.ownership("unowned/scratch.py") is None


def test_change_frequency_reflects_real_git_history(git_fixture_repo):
    repo_root, commits = git_fixture_repo
    idx = RepoIndex(repo_root, "fixture/multi-pkg-repo")
    idx.build()

    result = idx.ownership("pkg_a/billing/util.py")
    assert result is not None
    cf = result["change_frequency"]
    # util.py was touched by the recent (in-90-day-window) commit only
    # -- the old commit's initial-import touch is outside the window.
    assert cf["commits_last_90d"] == 1
    assert "Bea Recent" in cf["top_contributors"]

    # A file only touched by the (out-of-window) initial commit reports
    # zero commits in the last 90 days, but still has real history.
    result_service = idx.ownership("pkg_a/billing/service.py")
    cf_service = result_service["change_frequency"]
    assert cf_service["commits_last_90d"] == 0
    assert "Ada Old" in cf_service["top_contributors"]


def test_git_unavailable_is_a_real_condition_not_canned(tmp_path, monkeypatch):
    """git log genuinely failing (not a magic tenant id) surfaces as
    GitUnavailable -- service.py maps this to upstream-unavailable."""
    (tmp_path / "CODEOWNERS").write_text("f.py @someone\n")
    (tmp_path / "f.py").write_text("x = 1\n")
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_NAME": "X",
            "GIT_AUTHOR_EMAIL": "x@example.invalid",
            "GIT_COMMITTER_NAME": "X",
            "GIT_COMMITTER_EMAIL": "x@example.invalid",
        },
    )

    idx = RepoIndex(tmp_path, "broken-git-repo")
    idx.build()

    def _broken_run(repo_root, args):
        raise git_utils.GitUnavailable("simulated git binary failure")

    monkeypatch.setattr(git_utils, "_run", _broken_run)

    import pytest

    with pytest.raises(git_utils.GitUnavailable):
        idx.ownership("f.py")
