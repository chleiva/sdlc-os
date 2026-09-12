"""`AnthropicAgentBackend` -- a real `AgentBackend` implementation (see
`model_backend.py`) that speaks to Anthropic's Messages API
(`https://api.anthropic.com/v1/messages`), one of the three external,
API-key-authenticated vendors the Section 14.16 Docker Compose deployment
mode delegates model inference to when there is no self-hosted GPU to
serve `ornith-1.5-35b-a3b` from (master spec Section 13.8, "Multi-Vendor
API-Key Inference", New Rev 9). See `model_backend.py`'s and
`ollama_backend.py`'s module docstrings first -- same "real code, mocked
external boundary" discipline, same `AgentBackend` contract, just a
different vendor API shape.

**Structured output via forced tool-use, not free-form JSON.** The
Messages API has no request-time JSON-Schema-constrained decoding knob
the way Ollama's native `/api/chat` has its `format` field. The closest
equivalent Anthropic's API actually offers is the `tools` parameter with
`tool_choice` forced to a single named tool (`{"type": "tool", "name":
...}`): declare one tool whose `input_schema` is the exact JSON Schema
this module needs (`PlanOutput`'s/`DiffOutput`'s shape -- imported from
`ollama_backend.PLAN_OUTPUT_SCHEMA`/`DIFF_OUTPUT_SCHEMA`, not
redefined, since that schema describes `model_backend.PlanOutput`/
`DiffOutput`, not anything Ollama-specific), and force `tool_choice` to
that tool so the model has no path to reply except a `tool_use` content
block -- the same "constrained decoding, not just prompting" property
`ollama_backend.py` gets from `format`. (Forced `tool_choice` is a
model-specific feature, not a universal one -- some current Claude
models reject `tool_choice: {"type": "tool", ...}` outright; the pinned
default model below supports it. A future default-model change must
recheck this.)

**There is no live Anthropic API key/account in this environment** --
same constraint every vendor backend in this pass has (see
`model_backend.py`). This class is real, production-shaped client code:
real HTTP request/response construction against the real Messages API
request/response shape, real retry-with-backoff on rate-limiting (HTTP
429) and connection failures, and real parsing of the forced tool-use
response into the exact dataclasses `core.py` consumes. It is validated
in this pass against a local mock Anthropic HTTP server
(`tests/mock_anthropic_server.py`), never against the real endpoint --
same "real code, mocked external boundary" discipline as every other
deliverable in this system.

**Credential handling (Section 17.1).** `AnthropicBackendConfig.api_key`
is a plain constructor argument -- never read from an environment
variable inside this class (matching `OllamaBackendConfig`'s config
idiom) -- and is used *only* as the literal value of the `x-api-key`
HTTP header. It is never interpolated into a prompt, a log line, or any
exception message this module raises: every exception below is built
from the HTTP status code and the vendor's own response body/error
message, never from `self._config` as a whole, so a failed request
(auth failure included) cannot leak the key into anything a caller
prints or persists.

Uses only the standard library's `urllib` for HTTP, matching
`ollama_backend.py`'s and
`services/source-control/src/source_control/github_client.py`'s idiom
(this package has no `httpx`/`requests` dependency in `pyproject.toml`).
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from orchestrator.model_backend import (
    AcceptanceCriterion,
    AgentBackend,
    DiffOutput,
    PlanOutput,
    SubTask,
)
from orchestrator.ollama_backend import DIFF_OUTPUT_SCHEMA, PLAN_OUTPUT_SCHEMA

# A real, current Claude model id (see the model-selection reference this
# module was built against: `claude-sonnet-5`, Anthropic's current
# mid-tier model). Configurable per deployment (Section 13.8: "selected
# per deployment rather than hardcoded") -- this is only the default.
DEFAULT_MODEL = "claude-sonnet-5"

_ANTHROPIC_VERSION = "2023-06-01"
_MESSAGES_PATH = "/v1/messages"

_PLAN_TOOL_NAME = "emit_plan"
_DIFF_TOOL_NAME = "emit_diff"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AnthropicBackendError(Exception):
    """Base class for every error this module raises. See the module
    docstring's "Credential handling" section: no subclass below ever
    embeds `AnthropicBackendConfig`/`api_key` in its message -- only the
    HTTP status code and the vendor's own response body/error text."""


class AnthropicTransientError(AnthropicBackendError):
    """A retryable condition: the endpoint could not be reached at all
    (connection refused / DNS failure), a request timed out, or the
    endpoint responded with HTTP 429 (rate limited) or a 5xx server
    error -- retried with exponential backoff up to a configurable
    `max_retries` before being raised. Distinct from
    `AnthropicRequestError` on purpose: none of these conditions mean
    the request itself was wrong, so a caller should back off and retry
    later rather than treat this as a defect in the call."""


class AnthropicRequestError(AnthropicBackendError):
    """The endpoint responded, but with a non-retryable HTTP error
    status (e.g. 401 invalid API key, 400 malformed request, 404 unknown
    model). Not retried automatically -- retrying an unchanged bad
    request against a live server would just fail again the same way."""

    def __init__(self, message: str, *, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class AnthropicMalformedResponseError(AnthropicBackendError):
    """The endpoint responded 200 OK, but the response body was not
    valid JSON, did not contain the forced tool's `tool_use` content
    block, or that block's `input` did not conform to the
    `PlanOutput`/`DiffOutput` shape this method requires (missing
    required key, wrong type). Raised instead of best-effort string
    scraping -- a caller must not silently receive a half-populated
    dataclass."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnthropicBackendConfig:
    """Plain constructor-arg config surface -- not read from environment
    variables inside `AnthropicAgentBackend` (matches
    `OllamaBackendConfig`'s idiom in `ollama_backend.py`).

    `api_key` is never logged, never placed in a prompt, and never
    embedded in an exception message raised by this module -- see the
    module docstring's "Credential handling" section."""

    api_key: str
    model: str = DEFAULT_MODEL
    max_tokens: int = 4096
    base_url: str = "https://api.anthropic.com"
    timeout_seconds: float = 60.0
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0


# ---------------------------------------------------------------------------
# Response -> dataclass translation (mirrors `ollama_backend._plan_from_dict`/
# `_diff_from_dict` field-for-field, against the same imported schemas, but
# raises this module's own exception type -- see the module docstring on
# why the exception types are distinct per vendor).
# ---------------------------------------------------------------------------


def _require(d: dict, key: str, *, context: str) -> Any:
    if key not in d:
        raise AnthropicMalformedResponseError(f"{context}: missing required key {key!r} in {d!r}")
    return d[key]


def _plan_from_dict(d: dict) -> PlanOutput:
    if not isinstance(d, dict):
        raise AnthropicMalformedResponseError(f"expected a JSON object for a plan, got {type(d).__name__}: {d!r}")
    try:
        raw_criteria = _require(d, "acceptance_criteria", context="plan")
        criteria = tuple(
            AcceptanceCriterion(
                criterion_id=_require(c, "criterion_id", context="acceptance_criteria[]"),
                description=_require(c, "description", context="acceptance_criteria[]"),
                verification_tests=tuple(_require(c, "verification_tests", context="acceptance_criteria[]")),
            )
            for c in raw_criteria
        )
        raw_subtasks = _require(d, "subtasks", context="plan")
        subtasks = tuple(
            SubTask(
                task_id=_require(s, "task_id", context="subtasks[]"),
                description=_require(s, "description", context="subtasks[]"),
                parallel_group=s.get("parallel_group"),
                depends_on=tuple(s.get("depends_on") or ()),
                interface_contract=s.get("interface_contract"),
            )
            for s in raw_subtasks
        )
        return PlanOutput(
            outcomes=_require(d, "outcomes", context="plan"),
            acceptance_criteria=criteria,
            scope_in=tuple(_require(d, "scope_in", context="plan")),
            scope_out=tuple(_require(d, "scope_out", context="plan")),
            subtasks=subtasks,
            story_size=_require(d, "story_size", context="plan"),
            cross_cutting_or_high_risk=bool(_require(d, "cross_cutting_or_high_risk", context="plan")),
            risk_tier=_require(d, "risk_tier", context="plan"),
            rollback_strategy=_require(d, "rollback_strategy", context="plan"),
            constraints=d.get("constraints", ""),
            prior_decisions=d.get("prior_decisions", ""),
            open_questions=tuple(d.get("open_questions") or ()),
        )
    except AnthropicMalformedResponseError:
        raise
    except (TypeError, KeyError, AttributeError) as e:
        raise AnthropicMalformedResponseError(f"plan response did not match PlanOutput shape: {e}") from e


def _diff_from_dict(d: dict) -> DiffOutput:
    if not isinstance(d, dict):
        raise AnthropicMalformedResponseError(f"expected a JSON object for a diff, got {type(d).__name__}: {d!r}")
    try:
        return DiffOutput(
            files_touched=tuple(_require(d, "files_touched", context="diff")),
            lines_changed=int(_require(d, "lines_changed", context="diff")),
            commit_message=_require(d, "commit_message", context="diff"),
            subtask_id=d.get("subtask_id"),
        )
    except AnthropicMalformedResponseError:
        raise
    except (TypeError, KeyError, AttributeError, ValueError) as e:
        raise AnthropicMalformedResponseError(f"diff response did not match DiffOutput shape: {e}") from e


def _extract_tool_input(envelope: dict, *, tool_name: str) -> dict:
    """Pull the forced tool's `input` object out of a Messages API
    response envelope's `content` array. Anthropic validates a
    `tool_use` block's `input` against the tool's declared
    `input_schema` server-side before it reaches this client -- the same
    "constrained decoding, not just prompting" property `ollama_backend`
    gets from its own `format` field -- but this still checks the
    envelope/content shape defensively rather than indexing blindly,
    since a model can decline to call the forced tool at all (e.g. by
    replying with only a `text` block)."""
    if not isinstance(envelope, dict) or "content" not in envelope:
        raise AnthropicMalformedResponseError(f"response envelope missing 'content': {envelope!r}")
    content = envelope["content"]
    if not isinstance(content, list):
        raise AnthropicMalformedResponseError(f"response 'content' was not a list: {content!r}")
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == tool_name:
            tool_input = block.get("input")
            if not isinstance(tool_input, dict):
                raise AnthropicMalformedResponseError(
                    f"tool_use block for {tool_name!r} had a non-object 'input': {tool_input!r}"
                )
            return tool_input
    raise AnthropicMalformedResponseError(
        f"no {tool_name!r} tool_use block found in response content (forced tool_choice was not honored): {content!r}"
    )


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class AnthropicAgentBackend(AgentBackend):
    """Real `AgentBackend` client for Anthropic's Messages API. See
    module docstring for the forced-tool-use structured-output technique
    and what's real vs. mocked."""

    def __init__(self, config: AnthropicBackendConfig):
        self._config = config

    # -- AgentBackend interface -------------------------------------------------

    def author_plan(self, *, run_context: dict) -> PlanOutput:
        user_prompt = (
            "Produce a plan for the following run context.\n\n"
            f"run_context:\n{json.dumps(run_context, default=str, indent=2)}"
        )
        response = self._call_tool(
            user=user_prompt,
            tool_name=_PLAN_TOOL_NAME,
            tool_description="Emit the structured plan for this run.",
            schema=PLAN_OUTPUT_SCHEMA,
        )
        return _plan_from_dict(response)

    def re_plan(self, *, run_context: dict, feedback: str) -> PlanOutput:
        user_prompt = (
            "Revise the plan for the following run context in light of the "
            "reviewer feedback below. Do not begin implementing the prior "
            "plan; produce a full revised plan.\n\n"
            f"run_context:\n{json.dumps(run_context, default=str, indent=2)}\n\n"
            f"feedback:\n{feedback}"
        )
        response = self._call_tool(
            user=user_prompt,
            tool_name=_PLAN_TOOL_NAME,
            tool_description="Emit the structured, revised plan for this run.",
            schema=PLAN_OUTPUT_SCHEMA,
        )
        return _plan_from_dict(response)

    def implement_subtask(self, *, run_context: dict, subtask: SubTask) -> DiffOutput:
        user_prompt = (
            "Implement the following subtask and report the diff you produced.\n\n"
            f"run_context:\n{json.dumps(run_context, default=str, indent=2)}\n\n"
            "subtask:\n"
            f"{json.dumps({'task_id': subtask.task_id, 'description': subtask.description, 'parallel_group': subtask.parallel_group, 'depends_on': list(subtask.depends_on), 'interface_contract': subtask.interface_contract}, indent=2)}"
        )
        response = self._call_tool(
            user=user_prompt,
            tool_name=_DIFF_TOOL_NAME,
            tool_description="Emit the structured diff report for this subtask.",
            schema=DIFF_OUTPUT_SCHEMA,
        )
        diff = _diff_from_dict(response)
        if diff.subtask_id is None:
            diff = DiffOutput(
                files_touched=diff.files_touched,
                lines_changed=diff.lines_changed,
                commit_message=diff.commit_message,
                subtask_id=subtask.task_id,
            )
        return diff

    # -- HTTP plumbing -----------------------------------------------------

    def _call_tool(self, *, user: str, tool_name: str, tool_description: str, schema: dict) -> dict:
        """POST /v1/messages with a single tool declared and `tool_choice`
        forced to it, retrying on transient failures (connection errors,
        timeouts, HTTP 429/5xx), and return the forced tool's `input`
        object from the response."""
        request_body = json.dumps(
            {
                "model": self._config.model,
                "max_tokens": self._config.max_tokens,
                "messages": [{"role": "user", "content": user}],
                "tools": [
                    {
                        "name": tool_name,
                        "description": tool_description,
                        "input_schema": schema,
                    }
                ],
                "tool_choice": {"type": "tool", "name": tool_name},
            }
        ).encode("utf-8")

        url = f"{self._config.base_url.rstrip('/')}{_MESSAGES_PATH}"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._config.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
        }

        last_transient_error: Exception | None = None
        raw_body: str | None = None

        for attempt in range(self._config.max_retries):
            req = urllib.request.Request(url, data=request_body, method="POST", headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self._config.timeout_seconds) as resp:
                    raw_body = resp.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                if e.code == 429 or e.code >= 500:
                    # Rate-limited or a server-side error -- retryable,
                    # same posture as a connection failure below.
                    last_transient_error = e
                    if attempt < self._config.max_retries - 1:
                        time.sleep(self._config.retry_backoff_seconds * (2**attempt))
                        continue
                    raise AnthropicTransientError(
                        f"Anthropic endpoint returned HTTP {e.code} on every attempt "
                        f"({self._config.max_retries} attempt(s)): {_error_message(body)}"
                    ) from e
                raise AnthropicRequestError(
                    f"Anthropic endpoint returned HTTP {e.code}: {_error_message(body)}",
                    status_code=e.code,
                    body=body,
                ) from e
            except (urllib.error.URLError, socket.timeout, ConnectionRefusedError, TimeoutError) as e:
                # URLError wraps connection-refused/DNS failure and
                # socket.timeout wraps "connected but never responded in
                # time" -- both are transient, retried the same way.
                last_transient_error = e
                if attempt < self._config.max_retries - 1:
                    time.sleep(self._config.retry_backoff_seconds * (2**attempt))
                    continue
                raise AnthropicTransientError(
                    f"Anthropic endpoint at {self._config.base_url!r} did not respond after "
                    f"{self._config.max_retries} attempt(s): {e}"
                ) from e
            else:
                break
        else:  # pragma: no cover - loop always returns/raises above
            raise AnthropicTransientError(
                f"Anthropic endpoint at {self._config.base_url!r} did not respond: {last_transient_error}"
            )

        try:
            envelope = json.loads(raw_body)
        except json.JSONDecodeError as e:
            raise AnthropicMalformedResponseError(f"response body was not valid JSON: {raw_body!r}") from e

        return _extract_tool_input(envelope, tool_name=tool_name)


def _error_message(body: str) -> str:
    """Best-effort extraction of the vendor's own `error.message` field
    from an error response body, falling back to the raw body text. Only
    ever built from what the *server* sent back -- never from
    `self._config`/`api_key` -- so this cannot leak a credential even
    when the caller embeds the result in a log line."""
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return body
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict) and "message" in error:
            return str(error["message"])
    return body
