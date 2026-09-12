"""The real, runnable stdlib http.server wiring, exercised end to end
over an actual TCP socket -- not by calling `JobDispatcher` directly."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from job_dispatcher.http_app import serve

from conftest import PROJECT_A, SECRET_A, TENANT_A, signed_webhook


@pytest.fixture
def http_server(dispatcher):
    httpd = serve(dispatcher, host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _post(base_url, path, headers, body):
    req = urllib.request.Request(base_url + path, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_healthz(http_server):
    with urllib.request.urlopen(http_server + "/healthz", timeout=5) as resp:
        assert resp.status == 200
        assert json.loads(resp.read().decode("utf-8")) == {"status": "ok"}


def test_valid_signed_webhook_returns_202_run_created(http_server):
    body_obj = {
        "tenant_id": TENANT_A,
        "issue_key": "PROJA-7",
        "project_key": PROJECT_A,
        "repository": "acme/widgets",
        "status": "Selected for Development",
        "labels": ["ai-factory"],
    }
    headers, body = signed_webhook(tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj)

    status, payload = _post(http_server, "/webhook", headers, body)

    assert status == 202
    assert payload["outcome"] == "run_created"
    assert payload["tenant_id"] == TENANT_A
    assert payload["issue_key"] == "PROJA-7"
    assert payload["run_id"]


def test_unsigned_webhook_returns_401(http_server):
    body = b'{"tenant_id":"tenant-a","issue_key":"PROJA-1","project_key":"PROJA","repository":"r"}'
    status, payload = _post(http_server, "/webhook", {"Content-Type": "application/json"}, body)

    assert status == 401
    assert payload["reject_reason"] == "missing-tenant-id"


def test_stale_webhook_returns_401(http_server):
    body_obj = {
        "tenant_id": TENANT_A,
        "issue_key": "PROJA-7",
        "project_key": PROJECT_A,
        "repository": "acme/widgets",
        "status": "Selected for Development",
        "labels": ["ai-factory"],
    }
    headers, body = signed_webhook(
        tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj, now=time.time() - 400
    )

    status, payload = _post(http_server, "/webhook", headers, body)

    assert status == 401
    assert payload["reject_reason"] == "stale-timestamp"
