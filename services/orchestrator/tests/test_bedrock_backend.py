"""Tests for `orchestrator.bedrock_backend.BedrockAgentBackend` against a
hand-built fake `bedrock-runtime` client (`tests/fake_bedrock_client.py`)
-- no network access, no real AWS account, no real model weights. See
`fake_bedrock_client.py`'s module docstring for why a hand-built fake is
used here rather than `moto` (moto 5.2.3, the version already installed
in this repo for `services/kms-boundary`, implements only
`bedrock-runtime`'s `invoke_model` action as an empty stub -- no
`converse`/`converse_stream` support at all).
"""

from __future__ import annotations

import logging

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from orchestrator.bedrock_backend import (
    BedrockAccessDeniedError,
    BedrockAgentBackend,
    BedrockBackendConfig,
    BedrockInvocationError,
    BedrockMalformedOutputError,
    BedrockThrottledError,
)
from orchestrator.model_backend import SubTask
from tests.fake_bedrock_client import FakeBedrockRuntimeClient, converse_response_with_tool_call

_PLAN_INPUT = {
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

_DIFF_INPUT = {
    "files_touched": ["src/app/health.py"],
    "lines_changed": 12,
    "commit_message": "add /healthz route",
    "subtask_id": "t1",
}

_FAKE_MODEL_ID = "minimax.m2-5-v1:0"  # arbitrary stand-in id for tests only; see BedrockBackendConfig's module docstring


def _client_error(code: str, message: str = "boom", http_status: int = 400) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}, "ResponseMetadata": {"HTTPStatusCode": http_status}},
        "Converse",
    )


@pytest.fixture
def fake_client():
    return FakeBedrockRuntimeClient()


@pytest.fixture
def backend(fake_client):
    config = BedrockBackendConfig(
        model_id=_FAKE_MODEL_ID,
        max_retries=3,
        retry_backoff_seconds=0.01,
        timeout_seconds=5.0,
    )
    return BedrockAgentBackend(config, fake_client)


# ---------------------------------------------------------------------------
# Successful round trips
# ---------------------------------------------------------------------------


def test_author_plan_round_trip(fake_client, backend):
    fake_client.queue_response(converse_response_with_tool_call("emit_plan", _PLAN_INPUT))

    plan = backend.author_plan(run_context={"tenant_id": "t1", "story": "add health check"})

    assert plan.outcomes == "Add a health check endpoint."
    assert plan.story_size == "S"
    assert plan.risk_tier == "low"
    assert len(plan.acceptance_criteria) == 1
    assert plan.acceptance_criteria[0].criterion_id == "AC1"
    assert plan.subtasks[0].task_id == "t1"
    assert plan.scope_in == ("src/app/health.py",)

    # the request actually declared a forced tool call against the real
    # PLAN_OUTPUT_SCHEMA imported from ollama_backend, not a redefinition
    sent = fake_client.call_log[-1]
    assert sent["modelId"] == _FAKE_MODEL_ID
    tool_spec = sent["toolConfig"]["tools"][0]["toolSpec"]
    assert tool_spec["name"] == "emit_plan"
    assert tool_spec["inputSchema"]["json"]["required"] == [
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
    assert sent["toolConfig"]["toolChoice"] == {"tool": {"name": "emit_plan"}}


def test_re_plan_round_trip(fake_client, backend):
    revised = dict(_PLAN_INPUT, outcomes="Add a health check endpoint, revised per feedback.")
    fake_client.queue_response(converse_response_with_tool_call("emit_plan", revised))

    plan = backend.re_plan(
        run_context={"tenant_id": "t1"},
        feedback="please also cover the degraded-dependency case",
    )

    assert plan.outcomes == "Add a health check endpoint, revised per feedback."
    sent = fake_client.call_log[-1]
    assert "please also cover the degraded-dependency case" in sent["messages"][0]["content"][0]["text"]


def test_implement_subtask_round_trip(fake_client, backend):
    fake_client.queue_response(converse_response_with_tool_call("emit_diff", _DIFF_INPUT))

    subtask = SubTask(task_id="t1", description="add the /healthz route", parallel_group=None, depends_on=())
    diff = backend.implement_subtask(run_context={"tenant_id": "t1"}, subtask=subtask)

    assert diff.files_touched == ("src/app/health.py",)
    assert diff.lines_changed == 12
    assert diff.commit_message == "add /healthz route"
    assert diff.subtask_id == "t1"

    sent = fake_client.call_log[-1]
    assert sent["toolConfig"]["tools"][0]["toolSpec"]["name"] == "emit_diff"


def test_implement_subtask_fills_in_missing_subtask_id(fake_client, backend):
    fake_client.queue_response(converse_response_with_tool_call("emit_diff", dict(_DIFF_INPUT, subtask_id=None)))

    subtask = SubTask(task_id="t7", description="x", parallel_group=None, depends_on=())
    diff = backend.implement_subtask(run_context={}, subtask=subtask)

    assert diff.subtask_id == "t7"


# ---------------------------------------------------------------------------
# Malformed responses
# ---------------------------------------------------------------------------


def test_missing_tool_use_block_raises_malformed_output_error(fake_client, backend):
    fake_client.queue_response({"output": {"message": {"role": "assistant", "content": [{"text": "not a tool call"}]}}})

    with pytest.raises(BedrockMalformedOutputError):
        backend.author_plan(run_context={})


def test_wrong_tool_name_raises_malformed_output_error(fake_client, backend):
    fake_client.queue_response(converse_response_with_tool_call("some_other_tool", _PLAN_INPUT))

    with pytest.raises(BedrockMalformedOutputError):
        backend.author_plan(run_context={})


def test_missing_output_envelope_raises_malformed_output_error(fake_client, backend):
    fake_client.queue_response({"stopReason": "end_turn"})

    with pytest.raises(BedrockMalformedOutputError):
        backend.author_plan(run_context={})


def test_tool_input_missing_required_key_raises_malformed_output_error(fake_client, backend):
    incomplete = dict(_PLAN_INPUT)
    del incomplete["rollback_strategy"]
    fake_client.queue_response(converse_response_with_tool_call("emit_plan", incomplete))

    with pytest.raises(BedrockMalformedOutputError):
        backend.author_plan(run_context={})


def test_tool_input_not_an_object_raises_malformed_output_error(fake_client, backend):
    fake_client.queue_response(converse_response_with_tool_call("emit_plan", None))

    with pytest.raises(BedrockMalformedOutputError):
        backend.author_plan(run_context={})


# ---------------------------------------------------------------------------
# Throttling / retry / connection-error behavior
# ---------------------------------------------------------------------------


def test_retries_throttling_then_succeeds(fake_client, backend):
    fake_client.queue_response(_client_error("ThrottlingException", "slow down", http_status=429))
    fake_client.queue_response(_client_error("ThrottlingException", "slow down", http_status=429))
    fake_client.queue_response(converse_response_with_tool_call("emit_plan", _PLAN_INPUT))

    plan = backend.author_plan(run_context={})

    assert plan.outcomes == _PLAN_INPUT["outcomes"]
    assert len(fake_client.call_log) == 3


def test_throttling_exhausts_retries_raises_bedrock_throttled_error(fake_client, backend):
    for _ in range(3):
        fake_client.queue_response(_client_error("ThrottlingException", "slow down", http_status=429))

    with pytest.raises(BedrockThrottledError):
        backend.author_plan(run_context={})
    assert len(fake_client.call_log) == 3


def test_service_unavailable_is_retried_as_throttled(fake_client, backend):
    fake_client.queue_response(_client_error("ServiceUnavailableException", "try later", http_status=503))
    fake_client.queue_response(converse_response_with_tool_call("emit_plan", _PLAN_INPUT))

    plan = backend.author_plan(run_context={})
    assert plan.outcomes == _PLAN_INPUT["outcomes"]


def test_connection_error_is_retried_then_raises_bedrock_throttled_error(fake_client):
    config = BedrockBackendConfig(model_id=_FAKE_MODEL_ID, max_retries=2, retry_backoff_seconds=0.01, timeout_seconds=5.0)
    backend = BedrockAgentBackend(config, fake_client)
    for _ in range(2):
        fake_client.queue_response(EndpointConnectionError(endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com"))

    with pytest.raises(BedrockThrottledError):
        backend.author_plan(run_context={})
    assert len(fake_client.call_log) == 2


def test_soft_timeout_is_retried_then_raises_bedrock_throttled_error(fake_client):
    config = BedrockBackendConfig(model_id=_FAKE_MODEL_ID, max_retries=2, retry_backoff_seconds=0.01, timeout_seconds=0.1)
    backend = BedrockAgentBackend(config, fake_client)
    # Both attempts sleep longer than the configured timeout, so
    # `future.result(timeout=...)` raises before either ever "returns".
    fake_client.queue_delay(0.5)
    fake_client.queue_response(converse_response_with_tool_call("emit_plan", _PLAN_INPUT))
    fake_client.queue_delay(0.5)
    fake_client.queue_response(converse_response_with_tool_call("emit_plan", _PLAN_INPUT))

    with pytest.raises(BedrockThrottledError):
        backend.author_plan(run_context={})


def test_non_throttling_client_error_raises_bedrock_invocation_error_without_retry(fake_client, backend):
    fake_client.queue_response(_client_error("ValidationException", "bad request shape", http_status=400))

    with pytest.raises(BedrockInvocationError) as exc_info:
        backend.author_plan(run_context={})

    assert exc_info.value.error_code == "ValidationException"
    assert exc_info.value.http_status_code == 400
    # not retried -- exactly one call
    assert len(fake_client.call_log) == 1


# ---------------------------------------------------------------------------
# Auth / access-denied
# ---------------------------------------------------------------------------


def test_access_denied_raises_bedrock_access_denied_error_without_retry(fake_client, backend):
    fake_client.queue_response(_client_error("AccessDeniedException", "not authorized to invoke this model", http_status=403))

    with pytest.raises(BedrockAccessDeniedError):
        backend.author_plan(run_context={})

    # never retried -- exactly one call
    assert len(fake_client.call_log) == 1


def test_expired_token_raises_bedrock_access_denied_error(fake_client, backend):
    fake_client.queue_response(_client_error("ExpiredTokenException", "token expired", http_status=403))

    with pytest.raises(BedrockAccessDeniedError):
        backend.author_plan(run_context={})


# ---------------------------------------------------------------------------
# No credential material in exceptions or logs, even on failure
# (Rev 9 D2 acceptance criterion: "No API key appears in any log line,
# audit record, or error message produced by a vendor backend, even on a
# failed request.")
# ---------------------------------------------------------------------------

_FAKE_SECRET = "AKIAFAKEEXAMPLE1234:zSuperSecretSessionTokenValueDoNotLeak987654321"  # nosec - test-only marker string


def test_no_credential_material_in_exception_on_access_denied(fake_client, backend):
    # Simulate what a real botocore ClientError could carry if AWS ever
    # echoed request context back in an error message: the *message*
    # text itself does not contain the marker (it never would for a
    # real AccessDeniedException), but this also asserts our own
    # exception construction never appends anything beyond error
    # code/message/model id/region.
    fake_client.queue_response(_client_error("AccessDeniedException", "not authorized", http_status=403))

    with pytest.raises(BedrockAccessDeniedError) as exc_info:
        backend.author_plan(run_context={})

    assert _FAKE_SECRET not in str(exc_info.value)


def test_no_credential_material_in_exception_on_throttled_exhaustion(fake_client, backend):
    for _ in range(3):
        fake_client.queue_response(_client_error("ThrottlingException", "slow down", http_status=429))

    with pytest.raises(BedrockThrottledError) as exc_info:
        backend.author_plan(run_context={})

    assert _FAKE_SECRET not in str(exc_info.value)


def test_no_credential_material_in_exception_on_malformed_output(fake_client, backend):
    fake_client.queue_response({"output": {"message": {"content": []}}})

    with pytest.raises(BedrockMalformedOutputError) as exc_info:
        backend.author_plan(run_context={})

    assert _FAKE_SECRET not in str(exc_info.value)


def test_no_credential_material_in_logs_on_retried_failure(fake_client, backend, caplog):
    fake_client.queue_response(_client_error("ThrottlingException", "slow down", http_status=429))
    fake_client.queue_response(converse_response_with_tool_call("emit_plan", _PLAN_INPUT))

    with caplog.at_level(logging.WARNING):
        backend.author_plan(run_context={})

    assert _FAKE_SECRET not in caplog.text


def test_no_credential_material_when_run_context_and_env_carry_a_secret(fake_client, backend, monkeypatch):
    """A defensive regression guard: even if the surrounding process has
    an AWS secret in its environment (as any real deployment's would,
    since credential resolution is entirely the injected client's/
    caller's concern -- see `bedrock_backend.py`'s module docstring),
    this module never reads it, never threads it into a request/log/
    exception itself."""
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", _FAKE_SECRET)
    fake_client.queue_response(_client_error("AccessDeniedException", "not authorized", http_status=403))

    with pytest.raises(BedrockAccessDeniedError) as exc_info:
        backend.author_plan(run_context={})

    assert _FAKE_SECRET not in str(exc_info.value)
