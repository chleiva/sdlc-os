"""D3 acceptance criterion #4:

  Index refresh is incremental on commit, not a full rebuild.

Proven two ways: (a) git-diff-based refresh only reparses the file(s)
that actually changed between commits (RepoIndex.parse_count is the
instrumentation), and (b) the newly-added reference is actually picked
up by find_references after the refresh, so this isn't just a
bookkeeping no-op.
"""
from index_server.engine.repo_index import RepoIndex


def test_refresh_only_reparses_changed_files(git_fixture_repo):
    repo_root, commits = git_fixture_repo
    idx = RepoIndex(repo_root, "fixture/multi-pkg-repo")
    idx.build()
    assert idx.last_indexed_commit == commits["recent"]

    total_files = len(idx.file_records)
    assert total_files > 5  # sanity: this is a real multi-file build

    # Roll the index's bookkeeping back to the OLD commit (simulating
    # "we indexed at the old commit, and only now noticed HEAD moved"),
    # then add a brand-new cross-package call site and commit it.
    idx.last_indexed_commit = commits["old"]
    parse_count_before = idx.parse_count

    new_file = repo_root / "pkg_b" / "reporting" / "extra.py"
    new_file.write_text(
        "from pkg_a.billing.util import compute_total\n\n\ndef extra_caller(items):\n    return compute_total(items)\n"
    )
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=str(repo_root), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add extra caller"],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_NAME": "Carl New",
            "GIT_AUTHOR_EMAIL": "carl@example.invalid",
            "GIT_COMMITTER_NAME": "Carl New",
            "GIT_COMMITTER_EMAIL": "carl@example.invalid",
        },
    )

    result = idx.refresh()

    assert result.mode == "incremental"
    # Rolling last_indexed_commit back to "old" means the diff spans TWO
    # commits (the fixture's own "touch util.py" commit, plus this new
    # one) -- so exactly those two changed files are reparsed, and
    # nothing else: not a full rebuild of every file in the repo.
    assert result.reparsed == ["pkg_a/billing/util.py", "pkg_b/reporting/extra.py"]
    assert idx.parse_count == parse_count_before + 2
    assert idx.parse_count < total_files * 2  # nowhere near a full rebuild

    # And the new reference is actually live, not just bookkeeping.
    refs, known = idx.find_references("compute_total", {"file": "pkg_a/billing/util.py", "line": 7})
    assert known is True
    assert ("pkg_b/reporting/extra.py", 5) in {(r.file, r.line) for r in refs}


def test_refresh_is_noop_when_commit_unchanged(git_fixture_repo):
    repo_root, commits = git_fixture_repo
    idx = RepoIndex(repo_root, "fixture/multi-pkg-repo")
    idx.build()
    parse_count_after_build = idx.parse_count

    result = idx.refresh()
    assert result.mode == "noop"
    assert result.reparsed == []
    assert idx.parse_count == parse_count_after_build  # nothing reparsed at all


def test_refresh_without_git_falls_back_to_content_hash_diff(tmp_path):
    (tmp_path / "a.py").write_text("def a():\n    return 1\n")
    (tmp_path / "b.py").write_text("def b():\n    return 2\n")

    idx = RepoIndex(tmp_path, "no-git-repo")
    idx.build()
    assert idx.parse_count == 2

    # Unchanged content -- refresh must not reparse anything.
    result = idx.refresh()
    assert result.mode == "hash-diff"
    assert result.reparsed == []
    assert idx.parse_count == 2

    (tmp_path / "a.py").write_text("def a():\n    return 999\n")
    result = idx.refresh()
    assert result.reparsed == ["a.py"]
    assert idx.parse_count == 3  # only a.py was reparsed, not b.py too
