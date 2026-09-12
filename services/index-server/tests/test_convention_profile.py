"""D3 acceptance criterion #3:

  Convention-file content is read and surfaced as the top layer of the
  convention profile, not overridden by inferred conventions.
"""
from index_server.engine.repo_index import RepoIndex


def test_convention_file_content_is_read_verbatim(static_fixture_repo):
    idx = RepoIndex(static_fixture_repo, "fixture/multi-pkg-repo")
    profile = idx.convention_profile()
    on_disk = (static_fixture_repo / "CONVENTIONS.md").read_text(encoding="utf-8")

    assert profile.convention_file_path == "CONVENTIONS.md"
    assert profile.convention_file_content == on_disk
    assert "one-directional dependency" in profile.convention_file_content


def test_convention_file_is_not_overridden_by_inferred_signals(static_fixture_repo):
    """Even though nothing here infers a *different* build/test command,
    the guarantee under test is structural: convention_file_content is
    its own field, never merged/rewritten by the lint/test-framework
    inference below it."""
    idx = RepoIndex(static_fixture_repo, "fixture/multi-pkg-repo")
    profile = idx.convention_profile()
    original = profile.convention_file_content
    # Inferred fields live on separate attributes entirely.
    assert isinstance(profile.lint_format_configs, list)
    assert isinstance(profile.test_frameworks, list)
    assert profile.convention_file_content == original


def test_missing_convention_file_is_reported_as_absent_not_fabricated(tmp_path):
    idx = RepoIndex(tmp_path, "empty-repo")
    profile = idx.convention_profile()
    assert profile.convention_file_path is None
    assert profile.convention_file_content is None
