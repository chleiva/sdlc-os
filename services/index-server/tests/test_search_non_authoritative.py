"""D3 acceptance criterion #2:

  Search (semantic) results are structurally distinguishable from
  Find-references results (different response shape/label), so a
  caller can't mistake one for the other.
"""
from index_server.engine.repo_index import RepoIndex
from index_server.service import IndexService
from index_server.tenants import TenantRegistry

from pathlib import Path


def _service() -> IndexService:
    return IndexService(tenants=TenantRegistry())


def test_search_result_shape_differs_from_find_references_shape(static_fixture_repo):
    svc = _service()
    search_payload = svc.search({"tenant_id": "tenant-acme", "query": "compute total tax"})
    refs_payload = svc.find_references(
        {
            "tenant_id": "tenant-acme",
            "repository": "fixture/multi-pkg-repo",
            "symbol": "compute_total",
            "origin": {"file": "pkg_a/billing/util.py", "line": 7},
        }
    )

    assert search_payload["outcome"] == "ok"
    assert refs_payload["outcome"] == "ok"

    search_data = search_payload["data"]
    refs_data = refs_payload["data"]

    # Distinct, contract-mandated fields that make the two shapes
    # impossible to confuse with each other.
    assert search_data["authoritative"] is False
    assert search_data["label"] == "non-authoritative"
    assert "authoritative" not in refs_data
    assert "label" not in refs_data
    assert refs_data["exhaustive"] is True
    assert "exhaustive" not in search_data

    # Different result-item shape too: search items carry a fuzzy score,
    # find-references items carry an exact line/column locator.
    assert "score" in search_data["results"][0]
    assert "score" not in refs_data["references"][0]
    assert "line" in refs_data["references"][0] and "column" in refs_data["references"][0]


def test_search_is_labeled_non_authoritative_even_when_empty(static_fixture_repo):
    svc = _service()
    payload = svc.search({"tenant_id": "tenant-acme", "query": "xyzzy_no_such_thing_zzz"})
    assert payload["outcome"] == "empty"
    assert payload["authoritative"] is False
    assert payload["label"] == "non-authoritative"


def test_search_never_returned_as_if_it_were_find_references(static_fixture_repo):
    """A caller cannot get an `exhaustive` field out of search, and
    cannot get a `label`/`authoritative` field out of find-references --
    the schemas are disjoint on this dimension by construction."""
    from index_server.contract import load_index_schema

    schema = load_index_schema()
    search_ok = schema["tools"]["search"]["output_schema"]["oneOf"][0]["properties"]["data"]["properties"]
    refs_ok = schema["tools"]["find-references"]["output_schema"]["oneOf"][0]["properties"]["data"]["properties"]
    assert "authoritative" in search_ok and "authoritative" not in refs_ok
    assert "exhaustive" in refs_ok and "exhaustive" not in search_ok
