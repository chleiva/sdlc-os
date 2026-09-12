"""`OpenAIAgentBackend` -- a real `AgentBackend` implementation (see
`model_backend.py`) that speaks to OpenAI's own hosted Chat Completions
API (`https://api.openai.com/v1/chat/completions`).

**Why this exists (spec Section 13.8, New Rev 9).** Sections 13.1-13.7
specify self-hosted serving of the pinned reference model as the
default. The Section 14.16 Docker Compose deployment mode has no
standing GPU to serve that model from, so it substitutes model inference
delegated to an external, API-key-authenticated vendor -- Anthropic's
API, OpenAI's API, or Amazon Bedrock -- selected per deployment rather
than hardcoded. This module is the OpenAI-specific `AgentBackend`: same
three calls (`author_plan`/`re_plan`/`implement_subtask`) as every other
implementation of this interface, against OpenAI's own API shape. The
nine-stage workflow in `core.py` does not know or care that this
particular backend is configured -- see `model_backend.py`'s docstring
for the seam this plugs into.

**Structured-output technique: OpenAI's `response_format` with a strict
JSON Schema**, not the loose `{"type": "json_object"}` free-form-JSON
toggle -- `{"type": "json_schema", "json_schema": {"name": ..., "schema":
..., "strict": true}}` makes OpenAI's own decoder enforce that the
emitted JSON structurally conforms to the schema (required keys present,
correct types/enums, no extra keys), the same guarantee
`ollama_backend.py` gets from Ollama's grammar-constrained `format`
field, achieved through OpenAI's own mechanism instead. OpenAI's `strict`
mode has one structural requirement `ollama_backend.py`'s schemas were
not written to satisfy as-is: every object must set
`"additionalProperties": false` and list *every one* of its properties
in `"required"` (optionality is expressed by a property's own type
allowing `null`, never by omitting it from `required`). Rather than
hand-writing a second copy of `PLAN_OUTPUT_SCHEMA`/`DIFF_OUTPUT_SCHEMA`
to satisfy that, this module imports the exact same schema objects
`ollama_backend.py` already defines and derives a strict-mode-compliant
copy from them at request-construction time (`_to_strict_json_schema`,
below) -- one schema definition, two decoding backends.

**There is no live OpenAI account/API key in this environment** (same
constraint every vendor backend in this deliverable is under -- see
`model_backend.py`'s and `ollama_backend.py`'s docstrings). This class is
real, production-shaped client code: real HTTP request/response
construction, a real strict-JSON-schema `response_format`, real parsing
of the response into the exact `PlanOutput`/`DiffOutput` dataclasses
`core.py` consumes, real retry-with-backoff on rate-limiting and
transient connection failures, and dedicated exception types for a
non-retryable HTTP error, a retryable/transient failure, and a malformed
response -- validated in this pass against a local mock OpenAI HTTP
server (`tests/mock_openai_server.py`), never against the real API, same
"real code, mocked external boundary" discipline as every other
deliverable in this system.

**Credential handling (spec Section 17.1, and D2's Rev 9 acceptance
criterion "no API key appears in any log line, audit record, or error
message produced by a vendor backend, even on a failed request").** The
API key is a plain constructor argument (`OpenAIBackendConfig.api_key`),
never read from an environment variable inside this class, and is used
in exactly one place: the `Authorization` request header built fresh on
each attempt in `_complete`. It is never interpolated into any exception
message, any attribute of a raised exception, or any string this module
constructs from the config object -- `OpenAIBackendConfig.api_key` is
declared with `field(repr=False)` for defense in depth, so even an
accidental `repr(config)`/log of the whole config object omits it.

Uses only the standard library's `urllib` for HTTP, matching
`ollama_backend.py`'s idiom (this package has no `httpx`/`requests`
dependency in `pyproject.toml`, and stdlib is sufficient for plain
REST/JSON against a single host).
"""

from __future__ import annotations

import copy
import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from orchestrator.model_backend import AgentBackend, DiffOutput, PlanOutput, SubTask
from orchestrator.ollama_backend import (
    DIFF_OUTPUT_SCHEMA,
    PLAN_OUTPUT_SCHEMA,
    MalformedResponseError as _OllamaMalformedResponseError,
    _diff_from_dict,
    _plan_from_dict,
)

DEFAULT_MODEL = "gpt-4.1"
DEFAULT_BASE_URL = "https://api.openai.com"
_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"


# ---------------------------------------------------------------------------
# Exceptions
#
# Named for this vendor specifically (not a copy of ollama_backend.py's
# exact class names) since both backends may be imported side by side
# once the vendor-selection factory lands.
# ---------------------------------------------------------------------------


class OpenAIBackendError(Exception):
    """Base class for every error this module raises."""


class OpenAITransientError(OpenAIBackendError):
    """The request failed for a reason expected to be temporary --
    rate-limited (HTTP 429), a server-side error (HTTP 5xx), or a
    connection failure/timeout reaching the endpoint -- retried with
    exponential backoff up to the configured `max_retries` before this
    is raised. Distinct from `OpenAIRequestError` on purpose: a caller
    should back off and retry later, not treat this the same as a
    non-retryable client error."""


class OpenAIRequestError(OpenAIBackendError):
    """The endpoint responded with a non-retryable HTTP error status
    (e.g. 401 invalid API key, 400 malformed request, 404 unknown
    model). Not retried automatically -- retrying an unchanged bad
    request against a live server would just fail again.

    Never carries the API key: only the HTTP status code and the
    response body OpenAI itself sent back are attached, never the
    request's `Authorization` header."""

    def __init__(self, message: str, *, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class OpenAIMalformedResponseError(OpenAIBackendError):
    """The endpoint responded 200 OK, but the response body was not
    valid JSON, the chat-completion envelope was missing
    `choices[0].message.content`, the model returned no content (e.g. a
    refusal), or the content was valid JSON that did not conform to the
    `PlanOutput`/`DiffOutput` shape this method requires. Raised instead
    of best-effort string scraping -- a caller must not silently receive
    a half-populated dataclass."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenAIBackendConfig:
    """Plain constructor-arg config surface -- not read from environment
    variables inside `OpenAIAgentBackend` (matches `OllamaBackendConfig`'s
    idiom and this repo's config convention generally, e.g.
    `tenant_cell.model_diversity.TenantCellModelConfig`).

    `api_key` is declared `repr=False` so it never appears even in an
    accidental `repr()`/log of the config object itself -- defense in
    depth alongside this module never interpolating it into an
    exception message (see module docstring)."""

    api_key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    max_tokens: int = 4096
    timeout_seconds: float = 60.0
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0
    temperature: float = 0.0


# ---------------------------------------------------------------------------
# Strict-mode schema derivation -- reuses ollama_backend.py's
# PLAN_OUTPUT_SCHEMA/DIFF_OUTPUT_SCHEMA verbatim (imported, never
# redefined) and derives OpenAI `strict: true`-compliant copies from
# them: every object gets `additionalProperties: false` and every one of
# its properties added to `required` (nullable typing, already present
# on the optional fields in the imported schemas, is what expresses
# optionality under `strict` -- not omission from `required`).
# ---------------------------------------------------------------------------


def _make_strict(node: Any) -> None:
    if not isinstance(node, dict):
        return
    properties = node.get("properties")
    if isinstance(properties, dict):
        node["additionalProperties"] = False
        node["required"] = list(properties.keys())
        for value in properties.values():
            _make_strict(value)
    items = node.get("items")
    if isinstance(items, dict):
        _make_strict(items)


def _to_strict_json_schema(schema: dict) -> dict:
    """Return a deep copy of `schema` transformed to satisfy OpenAI's
    `strict: true` structured-output requirements. Does not mutate the
    input -- both `OpenAIAgentBackend` and `OllamaAgentBackend` share the
    same source schema objects."""
    strict_schema = copy.deepcopy(schema)
    _make_strict(strict_schema)
    return strict_schema


def _response_format_for(schema: dict, *, name: str) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "schema": _to_strict_json_schema(schema),
            "strict": True,
        },
    }


_PLAN_SYSTEM_PROMPT = (
    "You are the planning stage of an autonomous coding agent. Respond "
    "with ONLY a single JSON object matching the required schema -- no "
    "prose, no markdown fences, no commentary before or after the JSON."
)

_IMPLEMENT_SYSTEM_PROMPT = (
    "You are the implementation stage of an autonomous coding agent, "
    "reporting the diff you produced for one subtask. Respond with ONLY "
    "a single JSON object matching the required schema -- no prose, no "
    "markdown fences, no commentary before or after the JSON."
)


# ---------------------------------------------------------------------------
# Response -> dataclass translation
#
# Reuses ollama_backend.py's `_plan_from_dict`/`_diff_from_dict` (same
# generic dict -> PlanOutput/DiffOutput translation, vendor-agnostic)
# rather than re-implementing field-by-field mapping a second time; only
# the raised-exception type is rewrapped so a caller of this module never
# sees an `ollama_backend` exception class.
# ---------------------------------------------------------------------------


def _parse_json_content(content: str) -> Any:
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise OpenAIMalformedResponseError(f"message content was not valid JSON: {content!r}") from e


def _plan_from_content(content: str) -> PlanOutput:
    parsed = _parse_json_content(content)
    try:
        return _plan_from_dict(parsed)
    except _OllamaMalformedResponseError as e:
        raise OpenAIMalformedResponseError(str(e)) from e


def _diff_from_content(content: str) -> DiffOutput:
    parsed = _parse_json_content(content)
    try:
        return _diff_from_dict(parsed)
    except _OllamaMalformedResponseError as e:
        raise OpenAIMalformedResponseError(str(e)) from e


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class OpenAIAgentBackend(AgentBackend):
    """Real `AgentBackend` client for OpenAI's hosted Chat Completions
    API. See module docstring for structured-output technique, retry
    policy, and what's real vs. mocked."""

    def __init__(self, config: OpenAIBackendConfig):
        self._config = config

    # -- AgentBackend interface -------------------------------------------------

    def author_plan(self, *, run_context: dict) -> PlanOutput:
        user_prompt = (
            "Produce a plan for the following run context.\n\n"
            f"run_context:\n{json.dumps(run_context, default=str, indent=2)}"
        )
        content = self._complete(
            system=_PLAN_SYSTEM_PROMPT,
            user=user_prompt,
            schema=PLAN_OUTPUT_SCHEMA,
            schema_name="plan_output",
        )
        return _plan_from_content(content)

    def re_plan(self, *, run_context: dict, feedback: str) -> PlanOutput:
        user_prompt = (
            "Revise the plan for the following run context in light of the "
            "reviewer feedback below. Do not begin implementing the prior "
            "plan; produce a full revised plan.\n\n"
            f"run_context:\n{json.dumps(run_context, default=str, indent=2)}\n\n"
            f"feedback:\n{feedback}"
        )
        content = self._complete(
            system=_PLAN_SYSTEM_PROMPT,
            user=user_prompt,
            schema=PLAN_OUTPUT_SCHEMA,
            schema_name="plan_output",
        )
        return _plan_from_content(content)

    def implement_subtask(self, *, run_context: dict, subtask: SubTask) -> DiffOutput:
        user_prompt = (
            "Implement the following subtask and report the diff you produced.\n\n"
            f"run_context:\n{json.dumps(run_context, default=str, indent=2)}\n\n"
            "subtask:\n"
            f"{json.dumps({'task_id': subtask.task_id, 'description': subtask.description, 'parallel_group': subtask.parallel_group, 'depends_on': list(subtask.depends_on), 'interface_contract': subtask.interface_contract}, indent=2)}"
        )
        content = self._complete(
            system=_IMPLEMENT_SYSTEM_PROMPT,
            user=user_prompt,
            schema=DIFF_OUTPUT_SCHEMA,
            schema_name="diff_output",
        )
        diff = _diff_from_content(content)
        if diff.subtask_id is None:
            diff = DiffOutput(
                files_touched=diff.files_touched,
                lines_changed=diff.lines_changed,
                commit_message=diff.commit_message,
                subtask_id=subtask.task_id,
            )
        return diff

    # -- HTTP plumbing -----------------------------------------------------

    def _complete(self, *, system: str, user: str, schema: dict, schema_name: str) -> str:
        """POST /v1/chat/completions with a strict-JSON-schema
        `response_format`, retrying on rate limiting/server errors/
        connection failures, and return `choices[0].message.content`."""
        request_body = json.dumps(
            {
                "model": self._config.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": _response_format_for(schema, name=schema_name),
                "temperature": self._config.temperature,
                "max_tokens": self._config.max_tokens,
            }
        ).encode("utf-8")

        url = f"{self._config.base_url.rstrip('/')}{_CHAT_COMPLETIONS_PATH}"
        last_transient_error: Exception | None = None
        raw_body: str | None = None

        for attempt in range(self._config.max_retries):
            req = urllib.request.Request(
                url,
                data=request_body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    # The only place the API key is ever used. Never
                    # logged, never echoed into an exception message.
                    "Authorization": f"Bearer {self._config.api_key}",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self._config.timeout_seconds) as resp:
                    raw_body = resp.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                if e.code == 429 or e.code >= 500:
                    # Rate-limited or a server-side error: expected to be
                    # temporary, so retried the same as a connection
                    # failure rather than surfaced as a non-retryable
                    # client error.
                    last_transient_error = e
                    if attempt < self._config.max_retries - 1:
                        time.sleep(self._config.retry_backoff_seconds * (2**attempt))
                        continue
                    raise OpenAITransientError(
                        f"OpenAI endpoint returned HTTP {e.code} after "
                        f"{self._config.max_retries} attempt(s) (rate limited or "
                        "server error)"
                    ) from e
                raise OpenAIRequestError(
                    f"OpenAI endpoint returned HTTP {e.code}",
                    status_code=e.code,
                    body=body,
                ) from e
            except (urllib.error.URLError, socket.timeout, ConnectionRefusedError, TimeoutError) as e:
                # URLError wraps connection-refused/DNS failure; socket
                # timeout wraps "accepted the connection but never
                # responded in time" -- both are retried the same way.
                last_transient_error = e
                if attempt < self._config.max_retries - 1:
                    time.sleep(self._config.retry_backoff_seconds * (2**attempt))
                    continue
                raise OpenAITransientError(
                    f"OpenAI endpoint did not respond after "
                    f"{self._config.max_retries} attempt(s) (connection failure): {e}"
                ) from e
            else:
                break
        else:  # pragma: no cover - loop always returns/raises above
            raise OpenAITransientError(
                f"OpenAI endpoint did not respond after "
                f"{self._config.max_retries} attempt(s): {last_transient_error}"
            )

        assert raw_body is not None
        try:
            envelope = json.loads(raw_body)
        except json.JSONDecodeError as e:
            raise OpenAIMalformedResponseError("response body was not valid JSON") from e

        try:
            choices = envelope["choices"]
            message = choices[0]["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise OpenAIMalformedResponseError(
                f"response envelope missing choices[0].message.content: {envelope!r}"
            ) from e

        if content is None:
            raise OpenAIMalformedResponseError(
                "model returned no content in choices[0].message.content (possible refusal)"
            )

        return content
