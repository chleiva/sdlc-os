from index_server.engine.repo_index import RepoIndex


def _idx(root):
    idx = RepoIndex(root, "fixture/multi-pkg-repo")
    idx.build()
    return idx


def test_find_definition_from_cross_package_origin(static_fixture_repo):
    idx = _idx(static_fixture_repo)
    defs = idx.find_definition("compute_total", {"file": "pkg_b/reporting/report.py", "line": 10})
    assert len(defs) == 1
    d = defs[0]
    assert d.file == "pkg_a/billing/util.py"
    assert d.kind == "function"
    assert d.line == 7


def test_find_definition_for_class_method(static_fixture_repo):
    idx = _idx(static_fixture_repo)
    defs = idx.find_definition("total", {"file": "pkg_a/billing/util.py", "line": 18})
    assert len(defs) == 1
    assert defs[0].kind == "method"
    assert defs[0].fqn == "pkg_a.billing.util.Invoice.total"


def test_find_definition_unknown_symbol_returns_empty_list(static_fixture_repo):
    idx = _idx(static_fixture_repo)
    assert idx.find_definition("totally_made_up_symbol_xyz", None) == []


def test_find_callers_one_hop_reports_containing_symbol(static_fixture_repo):
    idx = _idx(static_fixture_repo)
    calls, cursor, known = idx.find_callers("compute_total", {"file": "pkg_a/billing/util.py", "line": 7})
    assert known is True
    assert cursor is None
    containing = {c.containing_symbol_name for c in calls}
    assert "render_receipt" in containing
    assert "monthly_report" in containing
    assert "legacy_total" in containing


def test_find_callers_pagination(static_fixture_repo):
    idx = _idx(static_fixture_repo)
    page1, cursor1, _ = idx.find_callers("compute_total", None, cursor=None, max_results=2)
    assert len(page1) == 2
    assert cursor1 is not None
    page2, cursor2, _ = idx.find_callers("compute_total", None, cursor=cursor1, max_results=2)
    assert len(page2) == 2
    assert {(c.file, c.line) for c in page1}.isdisjoint({(c.file, c.line) for c in page2})


def test_find_callers_no_callers_reports_unknown_false_only_when_symbol_missing(static_fixture_repo):
    idx = _idx(static_fixture_repo)
    calls, cursor, known = idx.find_callers("no_such_symbol_at_all", None)
    assert calls == []
    assert known is False

    # A real, defined function that legitimately has no callers anywhere
    # (an entry point / dead code) -- known True, empty list.
    calls2, cursor2, known2 = idx.find_callers("noop", {"file": "unowned/scratch.py", "line": 5})
    assert known2 is True
    assert calls2 == []
