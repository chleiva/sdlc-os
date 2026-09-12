"""Tests for `orchestrator.openai_backend.OpenAIAgentBackend` against the
local mock OpenAI server (`tests/mock_openai_server.py`) -- no network
access, no real OpenAI account, no real model weights. See
`openai_backend`'s module docstring for what's real (HTTP client, strict
JSON-schema request construction, response translation, retry/error
handling) vs. what a human still needs to do (supply a real API key and
point `base_url` at the real `https://api.openai.com`).
"""

from __future__ import annotations

import json

import pytest

from orchestrator.model_backend import SubTask
from orchestrator.openai_backend import (
    OpenAIAgentBackend,
    OpenAIBackendConfig,
    OpenAIMalformedResponseError,
    OpenAIRequestError,
    OpenAITransientError,
)
from tests.mock_openai_server import MockOpenAIServer

_FAKE_API_KEY = "sk-test-super-secret-do-not-leak-0123456789"

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
    server = MockOpenAIServer()
    base_url = server.start()
    yield server, base_url
    server.stop()


@pytest.fixture
def backend(mock_server):
    _server, base_url = mock_server
    config = OpenAIBackendConfig(
        api_key=_FAKE_API_KEY,
        base_url=base_url,
        max_retries=2,
        retry_backoff_seconds=0.01,
        timeout_seconds=5.0,
    )
    return OpenAIAgentBackend(config)


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

    # the request actually sent a strict JSON-schema response_format
    sent = server.state.call_log[-1]
    assert sent["model"] == "gpt-4.1"
    response_format = sent["response_format"]
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert json_schema["strict"] is True
    assert json_schema["name"] == "plan_output"
    schema = json_schema["schema"]
    assert schema["additionalProperties"] is False
    # strict mode requires *every* property present in "required",
    # including ones ollama_backend.py's schema treats as optional.
    assert set(schema["required"]) == set(schema["properties"].keys())
    assert "constraints" in schema["required"]

    # the API key went out as a real Bearer Authorization header
    assert server.state.received_auth_headers[-1] == f"Bearer {_FAKE_API_KEY}"


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

    sent = server.state.call_log[-1]
    json_schema = sent["response_format"]["json_schema"]
    assert json_schema["name"] == "diff_output"
    assert json_schema["schema"]["additionalProperties"] is False


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

    with pytest.raises(OpenAIMalformedResponseError):
        backend.author_plan(run_context={})


def test_non_json_message_content_raises_malformed_response_error(mock_server, backend):
    server, _ = mock_server
    server.state.next_content = "this is not json"

    with pytest.raises(OpenAIMalformedResponseError):
        backend.author_plan(run_context={})


def test_missing_choices_raises_malformed_response_error(mock_server, backend):
    server, _ = mock_server
    server.state.choices_broken = True

    with pytest.raises(OpenAIMalformedResponseError):
        backend.author_plan(run_context={})


def test_refusal_null_content_raises_malformed_response_error(mock_server, backend):
    server, _ = mock_server
    server.state.content_missing = True

    with pytest.raises(OpenAIMalformedResponseError):
        backend.author_plan(run_context={})


def test_json_missing_required_key_raises_malformed_response_error(mock_server, backend):
    server, _ = mock_server
    incomplete = dict(_PLAN_DICT)
    del incomplete["rollback_strategy"]
    server.state.next_content = json.dumps(incomplete)

    with pytest.raises(OpenAIMalformedResponseError):
        backend.author_plan(run_context={})


# ---------------------------------------------------------------------------
# HTTP errors: auth (non-retryable) vs. rate-limit (retryable)
# ---------------------------------------------------------------------------


def test_invalid_api_key_raises_openai_request_error_and_is_not_retried(mock_server, backend):
    server, _ = mock_server
    server.state.status_code = 401
    server.state.error_body = json.dumps({"error": {"message": "Incorrect API key provided", "type": "invalid_request_error"}})

    with pytest.raises(OpenAIRequestError) as exc_info:
        backend.author_plan(run_context={})

    assert exc_info.value.status_code == 401
    # exactly one attempt -- a 401 is never retried
    assert len(server.state.call_log) == 1


def test_rate_limit_exhausting_retries_raises_openai_transient_error(mock_server, backend):
    server, _ = mock_server
    server.state.status_code = 429
    server.state.error_body = json.dumps({"error": {"message": "Rate limit reached", "type": "rate_limit_error"}})

    with pytest.raises(OpenAITransientError):
        backend.author_plan(run_context={})

    # backend fixture is configured with max_retries=2
    assert len(server.state.call_log) == 2


def test_rate_limit_then_success_retries_and_returns_plan(mock_server, backend):
    server, base_url = mock_server

    # First request hits 429; the mock server deterministically clears
    # the rate limit on the 2nd call it receives, simulating a transient
    # rate limit that clears within the retry budget (no timing race).
    server.state.status_code = 429
    server.state.error_body = json.dumps({"error": {"message": "Rate limit reached", "type": "rate_limit_error"}})
    server.state.next_content = json.dumps(_PLAN_DICT)
    server.state.flip_to_success_after_calls = 2

    config = OpenAIBackendConfig(
        api_key=_FAKE_API_KEY,
        base_url=base_url,
        max_retries=3,
        retry_backoff_seconds=0.01,
        timeout_seconds=5.0,
    )
    backend = OpenAIAgentBackend(config)

    plan = backend.author_plan(run_context={})
    assert plan.outcomes == _PLAN_DICT["outcomes"]
    assert len(server.state.call_log) == 2


def test_server_error_status_is_retried_as_transient(mock_server, backend):
    server, _ = mock_server
    server.state.status_code = 503
    server.state.error_body = json.dumps({"error": {"message": "server overloaded", "type": "server_error"}})

    with pytest.raises(OpenAITransientError):
        backend.author_plan(run_context={})

    assert len(server.state.call_log) == 2  # backend fixture max_retries=2


def test_other_client_error_raises_openai_request_error(mock_server, backend):
    server, _ = mock_server
    server.state.status_code = 400
    server.state.error_body = json.dumps({"error": {"message": "bad request", "type": "invalid_request_error"}})

    with pytest.raises(OpenAIRequestError) as exc_info:
        backend.author_plan(run_context={})
    assert exc_info.value.status_code == 400
    assert len(server.state.call_log) == 1


# ---------------------------------------------------------------------------
# Connection failure
# ---------------------------------------------------------------------------


def test_connection_refused_raises_openai_transient_error(mock_server):
    server, base_url = mock_server
    server.stop()  # nothing is listening at base_url anymore

    config = OpenAIBackendConfig(
        api_key=_FAKE_API_KEY, base_url=base_url, max_retries=2, retry_backoff_seconds=0.01, timeout_seconds=1.0
    )
    backend = OpenAIAgentBackend(config)

    with pytest.raises(OpenAITransientError):
        backend.author_plan(run_context={})


def test_slow_response_times_out_and_raises_openai_transient_error(mock_server):
    server, base_url = mock_server
    server.state.delay_seconds = 2.0
    server.state.next_content = json.dumps(_PLAN_DICT)

    config = OpenAIBackendConfig(
        api_key=_FAKE_API_KEY, base_url=base_url, max_retries=2, retry_backoff_seconds=0.01, timeout_seconds=0.2
    )
    backend = OpenAIAgentBackend(config)

    with pytest.raises(OpenAITransientError):
        backend.author_plan(run_context={})


# ---------------------------------------------------------------------------
# Rev 9 acceptance criterion: the API key never appears in any exception
# message or log output, even on a failed request.
# ---------------------------------------------------------------------------


def _assert_key_not_leaked(exc: BaseException) -> None:
    assert _FAKE_API_KEY not in str(exc)
    for arg in exc.args:
        assert _FAKE_API_KEY not in str(arg)
    # walk the exception chain (__cause__/__context__) too
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        _assert_key_not_leaked(cause)


def test_api_key_not_leaked_on_auth_error(mock_server, backend):
    server, _ = mock_server
    server.state.status_code = 401
    server.state.error_body = json.dumps({"error": {"message": "Incorrect API key provided", "type": "invalid_request_error"}})

    with pytest.raises(OpenAIRequestError) as exc_info:
        backend.author_plan(run_context={})

    _assert_key_not_leaked(exc_info.value)
    assert _FAKE_API_KEY not in exc_info.value.body


def test_api_key_not_leaked_on_rate_limit_exhaustion(mock_server, backend):
    server, _ = mock_server
    server.state.status_code = 429
    server.state.error_body = json.dumps({"error": {"message": "Rate limit reached", "type": "rate_limit_error"}})

    with pytest.raises(OpenAITransientError) as exc_info:
        backend.author_plan(run_context={})

    _assert_key_not_leaked(exc_info.value)


def test_api_key_not_leaked_on_connection_failure(mock_server):
    server, base_url = mock_server
    server.stop()

    config = OpenAIBackendConfig(
        api_key=_FAKE_API_KEY, base_url=base_url, max_retries=1, retry_backoff_seconds=0.01, timeout_seconds=1.0
    )
    backend = OpenAIAgentBackend(config)

    with pytest.raises(OpenAITransientError) as exc_info:
        backend.author_plan(run_context={})

    _assert_key_not_leaked(exc_info.value)


def test_api_key_not_leaked_on_malformed_response(mock_server, backend):
    server, _ = mock_server
    server.state.next_content = "this is not json"

    with pytest.raises(OpenAIMalformedResponseError) as exc_info:
        backend.author_plan(run_context={})

    _assert_key_not_leaked(exc_info.value)


def test_api_key_not_in_config_repr(mock_server):
    _server, base_url = mock_server
    config = OpenAIBackendConfig(api_key=_FAKE_API_KEY, base_url=base_url)
    assert _FAKE_API_KEY not in repr(config)
    assert _FAKE_API_KEY not in str(config)
