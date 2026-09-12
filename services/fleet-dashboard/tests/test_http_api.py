"""Browser-less HTTP-level tests of the actual server (stdlib
`http.server`), started for real on a loopback port -- no mocking of the
HTTP layer itself.
"""

from __future__ import annotations

import json
import urllib.request
import uuid

import pytest
from run_registry import RegistryService

from fleet_dashboard import auth
from fleet_dashboard.dashboard_service import DashboardService
from fleet_dashboard.http_app import run_in_background
from tests.conftest import make_run


@pytest.fixture
def live_server(tmp_path):
    db_path = str(tmp_path / "registry.db")
    registry = RegistryService(db_path)
    service = DashboardService(registry)
    httpd, thread = run_in_background(service, host="127.0.0.1", port=0)
    port = httpd.server_address[1]
    yield registry, f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()
    registry.close()


def _get(url, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read())


def test_healthz(live_server):
    _, base = live_server
    status, body = _get(f"{base}/healthz")
    assert status == 200
    assert body["status"] == "ok"


def test_root_serves_the_static_dashboard_html(live_server):
    _, base = live_server
    req = urllib.request.Request(f"{base}/")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        body = resp.read().decode("utf-8")
    assert "<title>Fleet Control Dashboard</title>" in body


def test_cards_endpoint_scopes_by_identity_not_by_client_supplied_tenant_id(live_server):
    registry, base = live_server
    tenant_a = f"tenant-a-{uuid.uuid4().hex[:8]}"
    tenant_b = f"tenant-b-{uuid.uuid4().hex[:8]}"
    auth.register_identity_for_tests(auth.Identity(token=f"tok-a-{tenant_a}", tenant_ids=frozenset({tenant_a}), role="viewer"))

    run_a = make_run(registry, tenant_a, repo="org/a-repo")
    make_run(registry, tenant_b, repo="org/b-repo")

    token = f"tok-a-{tenant_a}"

    # No client-supplied tenant_id: identity resolves it unambiguously.
    status, body = _get(f"{base}/api/cards", token=token)
    assert status == 200
    assert {c["run_id"] for c in body["cards"]} == {run_a.id}

    # Malicious/mistaken client explicitly asks for tenant_b anyway --
    # must NOT be honored just because it's in the querystring.
    status, body = _get(f"{base}/api/cards?tenant_id={tenant_b}", token=token)
    assert status == 200
    assert body["cards"] == []
    assert tenant_b not in body["tenant_ids_viewed"]


def test_cards_endpoint_with_no_token_sees_nothing(live_server):
    registry, base = live_server
    make_run(registry, f"tenant-{uuid.uuid4().hex[:8]}")
    status, body = _get(f"{base}/api/cards")
    assert status == 200
    assert body["cards"] == []
    assert body["tenant_ids_viewed"] == []


def test_cards_endpoint_reflects_a_real_stage_change_over_http(live_server):
    registry, base = live_server
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    token = f"tok-{tenant_id}"
    auth.register_identity_for_tests(auth.Identity(token=token, tenant_ids=frozenset({tenant_id}), role="viewer"))

    run = make_run(registry, tenant_id)
    status, body = _get(f"{base}/api/cards", token=token)
    card = next(c for c in body["cards"] if c["run_id"] == run.id)
    assert card["stage"] == "intake"

    registry.transition_stage(
        tenant_id=tenant_id, run_id=run.id, expected_version=run.version, next_stage="research"
    )
    status, body = _get(f"{base}/api/cards", token=token)
    card = next(c for c in body["cards"] if c["run_id"] == run.id)
    assert card["stage"] == "research"


def test_filters_are_query_params_over_http(live_server):
    registry, base = live_server
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    token = f"tok-{tenant_id}"
    auth.register_identity_for_tests(auth.Identity(token=token, tenant_ids=frozenset({tenant_id}), role="viewer"))

    make_run(registry, tenant_id, repo="org/keep")
    make_run(registry, tenant_id, repo="org/drop")

    status, body = _get(f"{base}/api/cards?repository=org%2Fkeep", token=token)
    assert all(c["repository"] == "org/keep" for c in body["cards"])
    assert len(body["cards"]) == 1
