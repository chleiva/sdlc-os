"""`RovoClient` (Confluence research via the Rovo MCP pattern) against
the local mock. See rovo_client.py's docstring for the explicit
assumption flagged for a human about the exact tool names/schemas of
the real Rovo endpoint.
"""


def test_search_confluence_returns_ranked_non_authoritative_results(rovo_client, rovo_mock):
    _base_url, store = rovo_mock
    store.seed_page("111", title="Billing Architecture", space_key="ENG",
                     url="https://acme.atlassian.net/wiki/spaces/ENG/111", text="How tenant billing works.")
    store.seed_page("222", title="Unrelated Page", space_key="ENG",
                     url="https://acme.atlassian.net/wiki/spaces/ENG/222", text="Deployment runbook for the edge cache.")

    result = rovo_client.search_confluence(query="billing")
    assert result["outcome"] == "ok"
    assert result["data"]["non_authoritative"] is True
    keys = [r["page_id"] for r in result["data"]["results"]]
    assert "111" in keys
    assert "222" not in keys


def test_search_confluence_no_match_is_empty_result(rovo_client):
    result = rovo_client.search_confluence(query="nonexistent-topic-xyz")
    assert result["outcome"] == "empty"
    assert "reason" in result


def test_get_page_returns_full_content(rovo_client, rovo_mock):
    _base_url, store = rovo_mock
    store.seed_page("333", title="Deploy Runbook", space_key="OPS",
                     url="https://acme.atlassian.net/wiki/spaces/OPS/333", text="Full runbook text here.")

    result = rovo_client.get_page(page_id="333")
    assert result["outcome"] == "ok"
    assert result["data"]["title"] == "Deploy Runbook"
    assert result["data"]["body_text"] == "Full runbook text here."


def test_get_page_missing_is_empty_result(rovo_client):
    result = rovo_client.get_page(page_id="does-not-exist")
    assert result["outcome"] == "empty"


def test_unauthenticated_request_maps_to_permission_denied(rovo_mock):
    from issue_tracker.rovo_client import RovoClient, RovoConfig

    base_url, store = rovo_mock
    store.require_bearer_token = "the-real-token"
    client = RovoClient(config=RovoConfig(tenant_id="tenant-acme", base_url=base_url, oauth_bearer_token="wrong-token"))

    result = client.search_confluence(query="anything")
    assert result["outcome"] == "error"
    assert result["error"]["code"] == "permission-denied"


def test_upstream_unavailable_is_named_error(rovo_client, rovo_mock):
    _base_url, store = rovo_mock
    store.force_status = 503
    result = rovo_client.search_confluence(query="anything")
    assert result["outcome"] == "error"
    assert result["error"]["code"] == "upstream-unavailable"
    assert result["error"]["retryable"] is True
