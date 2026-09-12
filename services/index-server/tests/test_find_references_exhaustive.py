"""D3 acceptance criterion #1:

  Find-references on a renamed symbol returns every call site across a
  multi-package test fixture, including ones outside the "obviously
  affected" package.

`compute_total` is defined in pkg_a/billing/util.py. This proves the
rename-impact scenario end to end: every one of the 5 real call sites
across BOTH packages is returned, with no sampling/top-k cutoff, and a
same-named-but-different symbol is never conflated in.
"""
from pathlib import Path

from index_server.engine.repo_index import RepoIndex

ORIGIN = {"file": "pkg_a/billing/util.py", "line": 7}

EXPECTED_CALL_SITES = {
    ("pkg_a/billing/service.py", 7),          # same package as the definition
    ("pkg_a/billing/util.py", 18),             # same file, inside Invoice.total
    ("pkg_a/billing/tests/test_util.py", 12),  # test file
    ("pkg_b/reporting/report.py", 11),         # cross-package, direct `from ... import`
    ("pkg_b/reporting/legacy.py", 8),          # cross-package, `import ... as alias`
}


def _build_index(root: Path) -> RepoIndex:
    idx = RepoIndex(root, "fixture/multi-pkg-repo")
    idx.build()
    return idx


def test_exhaustive_across_packages(static_fixture_repo):
    idx = _build_index(static_fixture_repo)
    refs, known = idx.find_references("compute_total", ORIGIN)
    assert known is True
    got = {(r.file, r.line) for r in refs}
    assert got == EXPECTED_CALL_SITES, (
        f"missing: {EXPECTED_CALL_SITES - got}, unexpected: {got - EXPECTED_CALL_SITES}"
    )
    # Every reference outside pkg_a (the "obviously affected" package for
    # a rename scoped only to pkg_a) must still be present.
    outside_declared_scope = {(f, l) for (f, l) in got if not f.startswith("pkg_a/")}
    assert outside_declared_scope == {
        ("pkg_b/reporting/report.py", 11),
        ("pkg_b/reporting/legacy.py", 8),
    }


def test_no_sampling_cutoff_regardless_of_corpus_size(static_fixture_repo):
    """The result is never truncated to a top-k sample -- unlike
    find-callers (which paginates), find-references has no max_results
    knob at all in the contract, and this index never invents one."""
    idx = _build_index(static_fixture_repo)
    refs, _ = idx.find_references("compute_total", ORIGIN)
    assert len(refs) == len(EXPECTED_CALL_SITES)


def test_include_test_files_flag_excludes_test_reference(static_fixture_repo):
    idx = _build_index(static_fixture_repo)
    refs_with_tests, _ = idx.find_references("compute_total", ORIGIN, include_test_files=True)
    refs_without_tests, _ = idx.find_references("compute_total", ORIGIN, include_test_files=False)
    assert len(refs_without_tests) == len(refs_with_tests) - 1
    assert not any(r.file.endswith("test_util.py") for r in refs_without_tests)


def test_same_named_different_symbol_is_not_conflated(static_fixture_repo):
    """pkg_a/billing/util.py.Invoice.total() and pkg_b/web/view.ts's
    renderTotal() both use ordinary words, but there is no second
    `compute_total` anywhere -- this asserts the resolver isn't merely
    matching every occurrence of the bare word 'total'."""
    idx = _build_index(static_fixture_repo)
    refs, _ = idx.find_references("compute_total", ORIGIN)
    for r in refs:
        assert "compute_total" in r.context_snippet


def test_definition_site_itself_is_not_counted_as_a_reference(static_fixture_repo):
    idx = _build_index(static_fixture_repo)
    refs, _ = idx.find_references("compute_total", ORIGIN)
    assert not any(r.file == "pkg_a/billing/util.py" and r.line == 7 for r in refs)


def test_unknown_symbol_is_reported_as_not_known_but_not_an_error(static_fixture_repo):
    idx = _build_index(static_fixture_repo)
    refs, known = idx.find_references("this_symbol_does_not_exist_anywhere", None)
    assert refs == []
    assert known is False
