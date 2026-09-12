from index_server.service import IndexService


def _svc():
    return IndexService()


BASE = {"tenant_id": "tenant-acme", "repository": "fixture/multi-pkg-repo"}


def test_summary_for_generated_file_flags_generated(static_fixture_repo):
    payload = _svc().get_file_module_summary({**BASE, "path": "generated/schema_pb2.py"})
    assert payload["outcome"] == "ok"
    assert payload["data"]["generated"] is True
    assert payload["data"]["symbol_count"] == 3
    assert "not returned whole" in payload["data"]["summary"] or "generated" in payload["data"]["summary"].lower()


def test_summary_for_ordinary_file_not_flagged_generated(static_fixture_repo):
    payload = _svc().get_file_module_summary({**BASE, "path": "pkg_a/billing/util.py"})
    assert payload["outcome"] == "ok"
    assert payload["data"]["generated"] is False
    assert payload["data"]["symbol_count"] >= 3  # TAX_RATE, compute_total, Invoice (+ methods)


def test_summary_never_returns_full_file_contents(static_fixture_repo):
    payload = _svc().get_file_module_summary({**BASE, "path": "pkg_a/billing/util.py"})
    full_text = (static_fixture_repo / "pkg_a" / "billing" / "util.py").read_text()
    assert payload["data"]["summary"] != full_text
    assert len(payload["data"]["summary"]) < len(full_text) * 3  # a summary, not a restatement


def test_empty_file_reports_empty_outcome(static_fixture_repo):
    payload = _svc().get_file_module_summary({**BASE, "path": "pkg_a/billing/empty.py"})
    assert payload["outcome"] == "empty"
    assert "reason" in payload


def test_missing_path_is_not_found_error(static_fixture_repo):
    payload = _svc().get_file_module_summary({**BASE, "path": "pkg_a/billing/does_not_exist.py"})
    assert payload["outcome"] == "error"
    assert payload["error"]["code"] == "not-found"
