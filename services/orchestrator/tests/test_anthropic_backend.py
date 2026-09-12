"""Tests for `orchestrator.anthropic_backend.AnthropicAgentBackend` against
the local mock Anthropic server (`tests/mock_anthropic_server.py`) -- no
network access, no real Anthropic account, no real model. See
`anthropic_backend`'s module docstring for what's real (HTTP client,
forced-tool-use request/response translation, retry/error handling) vs.
what a human still needs to do (supply a real API key).
"""

from __future__ import annotations

import json

import pytest

from orchestrator.anthropic_backend import (
    AnthropicAgentBackend,
    AnthropicBackendConfig,
    AnthropicMalformedResponseError,
    AnthropicRequestError,
    AnthropicTransientError,
)
from orchestrator.model_backend import SubTask
from tests.mock_anthropic_server import MockAnthropicServer

_FAKE_API_KEY = "sk-ant-test-do-not-leak-me-0123456789"

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
    server = MockAnthropicServer()
    base_url = server.start()
    yield server, base_url
    server.stop()


@pytest.fixture
def backend(mock_server):
    _server, base_url = mock_server
    config = AnthropicBackendConfig(
        api_key=_FAKE_API_KEY,
        base_url=base_url,
        max_retries=2,
        retry_backoff_seconds=0.01,
        timeout_seconds=5.0,
    )
    return AnthropicAgentBackend(config)


# ---------------------------------------------------------------------------
# Successful round trips
# ---------------------------------------------------------------------------


def test_author_plan_round_trip(mock_server, backend):
    server, _ = mock_server
    server.state.next_tool_input = _PLAN_DICT

    plan = backend.author_plan(run_context={"tenant_id": "t1", "story": "add health check"})

    assert plan.outcomes == "Add a health check endpoint."
    assert plan.story_size == "S"
    assert plan.risk_tier == "low"
    assert len(plan.acceptance_criteria) == 1
    assert plan.acceptance_criteria[0].criterion_id == "AC1"
    assert plan.subtasks[0].task_id == "t1"
    assert plan.scope_in == ("src/app/health.py",)

    # the request actually sent a forced tool_choice with the schema-
    # constrained tool, the configured model, and the real API key header.
    sent = server.state.call_log[-1]
    assert sent["headers"]["x-api-key"] == _FAKE_API_KEY
    assert sent["headers"]["anthropic-version"] == "2023-06-01"
    assert sent["body"]["model"] == "claude-sonnet-5"
    assert sent["body"]["tool_choice"] == {"type": "tool", "name": "emit_plan"}
    tool = sent["body"]["tools"][0]
    assert tool["name"] == "emit_plan"
    assert tool["input_schema"]["required"] == [
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


def test_re_plan_round_trip(mock_server, backend):
    server, _ = mock_server
    revised = dict(_PLAN_DICT, outcomes="Add a health check endpoint, revised per feedback.")
    server.state.next_tool_input = revised

    plan = backend.re_plan(
        run_context={"tenant_id": "t1"},
        feedback="please also cover the degraded-dependency case",
    )

    assert plan.outcomes == "Add a health check endpoint, revised per feedback."
    sent = server.state.call_log[-1]
    assert "please also cover the degraded-dependency case" in sent["body"]["messages"][0]["content"]
    assert sent["body"]["tool_choice"] == {"type": "tool", "name": "emit_plan"}


def test_implement_subtask_round_trip(mock_server, backend):
    server, _ = mock_server
    server.state.next_tool_input = _DIFF_DICT

    subtask = SubTask(task_id="t1", description="add the /healthz route", parallel_group=None, depends_on=())
    diff = backend.implement_subtask(run_context={"tenant_id": "t1"}, subtask=subtask)

    assert diff.files_touched == ("src/app/health.py",)
    assert diff.lines_changed == 12
    assert diff.commit_message == "add /healthz route"
    assert diff.subtask_id == "t1"

    sent = server.state.call_log[-1]
    assert sent["body"]["tool_choice"] == {"type": "tool", "name": "emit_diff"}


def test_implement_subtask_fills_in_missing_subtask_id(mock_server, backend):
    server, _ = mock_server
    server.state.next_tool_input = dict(_DIFF_DICT, subtask_id=None)

    subtask = SubTask(task_id="t7", description="x", parallel_group=None, depends_on=())
    diff = backend.implement_subtask(run_context={}, subtask=subtask)

    assert diff.subtask_id == "t7"


# ---------------------------------------------------------------------------
# Malformed / non-conforming responses
# ---------------------------------------------------------------------------


def test_non_json_http_body_raises_malformed_response_error(mock_server, backend):
    server, _ = mock_server
    server.state.raw_body_broken = True

    with pytest.raises(AnthropicMalformedResponseError):
        backend.author_plan(run_context={})


def test_missing_tool_use_block_raises_malformed_response_error(mock_server, backend):
    server, _ = mock_server
    server.state.tool_use_missing = True

    with pytest.raises(AnthropicMalformedResponseError):
        backend.author_plan(run_context={})


def test_wrong_tool_name_raises_malformed_response_error(mock_server, backend):
    server, _ = mock_server
    server.state.next_tool_input = _PLAN_DICT
    server.state.wrong_tool_name = "some_other_tool"

    with pytest.raises(AnthropicMalformedResponseError):
        backend.author_plan(run_context={})


def test_json_missing_required_key_raises_malformed_response_error(mock_server, backend):
    server, _ = mock_server
    incomplete = dict(_PLAN_DICT)
    del incomplete["rollback_strategy"]
    server.state.next_tool_input = incomplete

    with pytest.raises(AnthropicMalformedResponseError):
        backend.author_plan(run_context={})


def test_non_object_tool_input_raises_malformed_response_error(mock_server, backend):
    server, _ = mock_server
    # Simulate a non-conforming payload: `input` present but not an object.
    server.state.next_tool_input = "not-an-object"  # type: ignore[assignment]

    with pytest.raises(AnthropicMalformedResponseError):
        backend.author_plan(run_context={})


# ---------------------------------------------------------------------------
# HTTP error statuses: auth failure (non-retryable) and rate limiting
# (retryable/transient)
# ---------------------------------------------------------------------------


def test_auth_error_raises_request_error_without_retry(mock_server, backend):
    server, _ = mock_server
    server.state.status_code = 401

    with pytest.raises(AnthropicRequestError) as exc_info:
        backend.author_plan(run_context={})

    assert exc_info.value.status_code == 401
    # Not retried: exactly one request should have been sent.
    assert len(server.state.call_log) == 1


def test_persistent_rate_limit_raises_transient_error_after_retries(mock_server):
    server, base_url = mock_server
    server.state.status_code = 429

    config = AnthropicBackendConfig(
        api_key=_FAKE_API_KEY, base_url=base_url, max_retries=3, retry_backoff_seconds=0.01, timeout_seconds=5.0
    )
    backend = AnthropicAgentBackend(config)

    with pytest.raises(AnthropicTransientError):
        backend.author_plan(run_context={})

    # Retried up to max_retries before giving up.
    assert len(server.state.call_log) == 3


def test_rate_limit_retries_then_succeeds(mock_server):
    server, base_url = mock_server
    server.state.transient_fail_count = 1  # first attempt 429s, second succeeds
    server.state.next_tool_input = _PLAN_DICT

    config = AnthropicBackendConfig(
        api_key=_FAKE_API_KEY, base_url=base_url, max_retries=3, retry_backoff_seconds=0.01, timeout_seconds=5.0
    )
    backend = AnthropicAgentBackend(config)

    plan = backend.author_plan(run_context={})

    assert plan.outcomes == _PLAN_DICT["outcomes"]
    assert len(server.state.call_log) == 2


def test_other_http_error_status_raises_request_error(mock_server, backend):
    server, _ = mock_server
    server.state.status_code = 400

    with pytest.raises(AnthropicRequestError) as exc_info:
        backend.author_plan(run_context={})
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Connection failure / timeout
# ---------------------------------------------------------------------------


def test_connection_refused_raises_transient_error(mock_server):
    server, base_url = mock_server
    server.stop()  # nothing is listening at base_url anymore

    config = AnthropicBackendConfig(
        api_key=_FAKE_API_KEY, base_url=base_url, max_retries=2, retry_backoff_seconds=0.01, timeout_seconds=1.0
    )
    backend = AnthropicAgentBackend(config)

    with pytest.raises(AnthropicTransientError):
        backend.author_plan(run_context={})


def test_slow_response_times_out_and_raises_transient_error(mock_server):
    server, base_url = mock_server
    server.state.delay_seconds = 2.0
    server.state.next_tool_input = _PLAN_DICT

    config = AnthropicBackendConfig(
        api_key=_FAKE_API_KEY, base_url=base_url, max_retries=2, retry_backoff_seconds=0.01, timeout_seconds=0.2
    )
    backend = AnthropicAgentBackend(config)

    with pytest.raises(AnthropicTransientError):
        backend.author_plan(run_context={})


# ---------------------------------------------------------------------------
# Acceptance criterion (Rev 9): the API key never appears in any exception
# message, even on a failed request.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "make_backend_and_trigger",
    [
        "auth_error",
        "rate_limited_exhausted",
        "malformed_response",
        "connection_refused",
    ],
)
def test_api_key_never_appears_in_exception_message(mock_server, make_backend_and_trigger):
    server, base_url = mock_server
    config = AnthropicBackendConfig(
        api_key=_FAKE_API_KEY, base_url=base_url, max_retries=2, retry_backoff_seconds=0.01, timeout_seconds=1.0
    )
    backend = AnthropicAgentBackend(config)

    if make_backend_and_trigger == "auth_error":
        server.state.status_code = 401
    elif make_backend_and_trigger == "rate_limited_exhausted":
        server.state.status_code = 429
    elif make_backend_and_trigger == "malformed_response":
        server.state.raw_body_broken = True
    elif make_backend_and_trigger == "connection_refused":
        server.stop()

    caught = None
    try:
        backend.author_plan(run_context={"tenant_id": "t1"})
    except Exception as e:  # noqa: BLE001 - deliberately broad: assert on whatever was raised
        caught = e

    assert caught is not None, "expected an exception to be raised"
    assert _FAKE_API_KEY not in str(caught)
    # Also check every chained/underlying exception's message and args, and
    # any exception attributes that carry raw text (e.g. AnthropicRequestError.body).
    node = caught
    while node is not None:
        assert _FAKE_API_KEY not in str(node)
        for arg in getattr(node, "args", ()):
            assert _FAKE_API_KEY not in str(arg)
        body = getattr(node, "body", "")
        assert _FAKE_API_KEY not in str(body)
        node = node.__cause__

    # Confirm the key really was sent as the request header (functional
    # correctness), distinct from it never leaking into error text.
    if make_backend_and_trigger != "connection_refused" and server.state.call_log:
        assert server.state.call_log[-1]["headers"]["x-api-key"] == _FAKE_API_KEY


def test_api_key_never_appears_in_repr_of_config_used_in_error_path(mock_server):
    """A defense-in-depth check: even the config object's own repr (which
    a careless log statement might include) is never referenced by this
    module's exception construction -- verified by confirming the raised
    exception text is independent of whatever `repr(config)` happens to
    contain."""
    server, base_url = mock_server
    server.state.status_code = 401
    config = AnthropicBackendConfig(api_key=_FAKE_API_KEY, base_url=base_url, max_retries=1, timeout_seconds=1.0)
    backend = AnthropicAgentBackend(config)

    with pytest.raises(AnthropicRequestError) as exc_info:
        backend.author_plan(run_context={})

    assert _FAKE_API_KEY not in str(exc_info.value)
    assert _FAKE_API_KEY not in repr(exc_info.value)


def test_malformed_response_error_message_is_diagnostic_but_key_free(mock_server, backend):
    """Sanity check that malformed-response errors still carry useful
    diagnostic content (this isn't satisfied by an empty message)."""
    server, _ = mock_server
    incomplete = dict(_PLAN_DICT)
    del incomplete["rollback_strategy"]
    server.state.next_tool_input = incomplete

    with pytest.raises(AnthropicMalformedResponseError) as exc_info:
        backend.author_plan(run_context={})

    assert "rollback_strategy" in str(exc_info.value)
    assert _FAKE_API_KEY not in str(exc_info.value)


def test_json_serializable_run_context_round_trips_through_request(mock_server, backend):
    server, _ = mock_server
    server.state.next_tool_input = _PLAN_DICT

    backend.author_plan(run_context={"tenant_id": "t9", "nested": {"a": 1}})

    sent = server.state.call_log[-1]["body"]
    user_content = sent["messages"][0]["content"]
    assert "t9" in user_content
    payload = json.loads(user_content.split("run_context:\n", 1)[1])
    assert payload["tenant_id"] == "t9"
    assert payload["nested"] == {"a": 1}
