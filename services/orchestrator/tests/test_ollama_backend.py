"""Tests for `orchestrator.ollama_backend.OllamaAgentBackend` against the
local mock Ollama server (`tests/mock_ollama_server.py`) -- no network
access, no real Ollama instance, no real model weights. See
`ollama_backend`'s module docstring for what's real (HTTP client,
request/response translation, retry/error handling) vs. what a human
still needs to do (point `base_url` at a real deployed Ollama instance).
"""

from __future__ import annotations

import json

import pytest

from orchestrator.model_backend import SubTask
from orchestrator.ollama_backend import (
    MalformedResponseError,
    ModelNotReadyError,
    OllamaAgentBackend,
    OllamaBackendConfig,
    OllamaRequestError,
)
from tests.mock_ollama_server import MockOllamaServer

_PLAN_DICT = {
    "outcomes": "Add a health check endpoint.",
    "acceptance_criteria": [
        {
            "criterion_id": "AC1",
            "description": "GET /healthz returns 200",
            "verification_tests": ["tests/test_health.py::test_healthz"],
        }
    ],
    "scope_in": ["src/app/health.py"],
    "scope_out": ["src/app/auth.py"],
    "subtasks": [
        {
            "task_id": "t1",
            "description": "add the /healthz route",
            "parallel_group": None,
            "depends_on": [],
            "interface_contract": None,
        }
    ],
    "story_size": "S",
    "cross_cutting_or_high_risk": False,
    "risk_tier": "low",
    "rollback_strategy": "git revert the merge commit",
    "constraints": "",
    "prior_decisions": "",
    "open_questions": [],
}

_DIFF_DICT = {
    "files_touched": ["src/app/health.py"],
    "lines_changed": 12,
    "commit_message": "add /healthz route",
    "subtask_id": "t1",
}


@pytest.fixture
def mock_server():
    server = MockOllamaServer()
    base_url = server.start()
    yield server, base_url
    server.stop()


@pytest.fixture
def backend(mock_server):
    _server, base_url = mock_server
    config = OllamaBackendConfig(base_url=base_url, max_retries=2, retry_backoff_seconds=0.01, timeout_seconds=5.0)
    return OllamaAgentBackend(config)


# ---------------------------------------------------------------------------
# Successful round trips
# ---------------------------------------------------------------------------


def test_author_plan_round_trip(mock_server, backend):
    server, _ = mock_server
    server.state.next_content = json.dumps(_PLAN_DICT)

    plan = backend.author_plan(run_context={"tenant_id": "t1", "story": "add health check"})

    assert plan.outcomes == "Add a health check endpoint."
    assert plan.story_size == "S"
    assert plan.risk_tier == "low"
    assert len(plan.acceptance_criteria) == 1
    assert plan.acceptance_criteria[0].criterion_id == "AC1"
    assert plan.subtasks[0].task_id == "t1"
    assert plan.scope_in == ("src/app/health.py",)

    # the request actually sent the required JSON-schema-constrained format
    sent = server.state.call_log[-1]
    assert sent["model"] == "ornith-1.5-35b-a3b"
    assert sent["format"]["required"] == [
        "outcomes",
        "acceptance_criteria",
        "scope_in",
        "scope_out",
        "subtasks",
        "story_size",
        "cross_cutting_or_high_risk",
        "risk_tier",
        "rollback_strategy",
    ]
    assert sent["stream"] is False


def test_re_plan_round_trip(mock_server, backend):
    server, _ = mock_server
    revised = dict(_PLAN_DICT, outcomes="Add a health check endpoint, revised per feedback.")
    server.state.next_content = json.dumps(revised)

    plan = backend.re_plan(
        run_context={"tenant_id": "t1"},
        feedback="please also cover the degraded-dependency case",
    )

    assert plan.outcomes == "Add a health check endpoint, revised per feedback."
    sent = server.state.call_log[-1]
    assert "please also cover the degraded-dependency case" in sent["messages"][1]["content"]


def test_implement_subtask_round_trip(mock_server, backend):
    server, _ = mock_server
    server.state.next_content = json.dumps(_DIFF_DICT)

    subtask = SubTask(task_id="t1", description="add the /healthz route", parallel_group=None, depends_on=())
    diff = backend.implement_subtask(run_context={"tenant_id": "t1"}, subtask=subtask)

    assert diff.files_touched == ("src/app/health.py",)
    assert diff.lines_changed == 12
    assert diff.commit_message == "add /healthz route"
    assert diff.subtask_id == "t1"


def test_implement_subtask_fills_in_missing_subtask_id(mock_server, backend):
    server, _ = mock_server
    server.state.next_content = json.dumps(dict(_DIFF_DICT, subtask_id=None))

    subtask = SubTask(task_id="t7", description="x", parallel_group=None, depends_on=())
    diff = backend.implement_subtask(run_context={}, subtask=subtask)

    assert diff.subtask_id == "t7"


# ---------------------------------------------------------------------------
# Malformed responses
# ---------------------------------------------------------------------------


def test_non_json_http_body_raises_malformed_response_error(mock_server, backend):
    server, _ = mock_server
    server.state.raw_body_broken = True

    with pytest.raises(MalformedResponseError):
        backend.author_plan(run_context={})


def test_non_json_message_content_raises_malformed_response_error(mock_server, backend):
    server, _ = mock_server
    server.state.next_content = "this is not json"

    with pytest.raises(MalformedResponseError):
        backend.author_plan(run_context={})


def test_missing_message_envelope_raises_malformed_response_error(mock_server, backend):
    server, _ = mock_server
    server.state.envelope_broken = True

    with pytest.raises(MalformedResponseError):
        backend.author_plan(run_context={})


def test_json_missing_required_key_raises_malformed_response_error(mock_server, backend):
    server, _ = mock_server
    incomplete = dict(_PLAN_DICT)
    del incomplete["rollback_strategy"]
    server.state.next_content = json.dumps(incomplete)

    with pytest.raises(MalformedResponseError):
        backend.author_plan(run_context={})


def test_http_error_status_raises_ollama_request_error(mock_server, backend):
    server, _ = mock_server
    server.state.status_code = 400

    with pytest.raises(OllamaRequestError) as exc_info:
        backend.author_plan(run_context={})
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Not-ready / cold-start / timeout
# ---------------------------------------------------------------------------


def test_connection_refused_raises_model_not_ready_error(mock_server):
    server, base_url = mock_server
    server.stop()  # nothing is listening at base_url anymore

    config = OllamaBackendConfig(base_url=base_url, max_retries=2, retry_backoff_seconds=0.01, timeout_seconds=1.0)
    backend = OllamaAgentBackend(config)

    with pytest.raises(ModelNotReadyError):
        backend.author_plan(run_context={})


def test_slow_cold_starting_model_times_out_and_raises_model_not_ready_error(mock_server):
    server, base_url = mock_server
    server.state.delay_seconds = 2.0
    server.state.next_content = json.dumps(_PLAN_DICT)

    config = OllamaBackendConfig(base_url=base_url, max_retries=2, retry_backoff_seconds=0.01, timeout_seconds=0.2)
    backend = OllamaAgentBackend(config)

    with pytest.raises(ModelNotReadyError):
        backend.author_plan(run_context={})


def test_retries_transient_failure_then_succeeds(mock_server):
    server, base_url = mock_server
    server.state.status_code = 200
    server.state.next_content = json.dumps(_PLAN_DICT)
    # First attempt times out (delay exceeds timeout), then the mock
    # server itself removes the delay for the retried attempt.
    server.state.delay_seconds = 0.3

    config = OllamaBackendConfig(base_url=base_url, max_retries=3, retry_backoff_seconds=0.01, timeout_seconds=0.1)
    backend = OllamaAgentBackend(config)

    # Flip off the delay from a background thread shortly after the first
    # attempt should have timed out, simulating the model finishing its
    # cold start in time for the second retry.
    import threading
    import time

    def clear_delay():
        time.sleep(0.15)
        server.state.delay_seconds = 0.0

    threading.Thread(target=clear_delay, daemon=True).start()

    plan = backend.author_plan(run_context={})
    assert plan.outcomes == _PLAN_DICT["outcomes"]
