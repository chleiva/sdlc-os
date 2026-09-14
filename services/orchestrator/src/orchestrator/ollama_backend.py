"""`OllamaAgentBackend` -- a real `AgentBackend` implementation (see
`model_backend.py`) that speaks to a self-hosted Ollama instance serving
the pinned model `ornith-1.5-35b-a3b` (35B total / ~3B active MoE,
Q4_K_M, 256K context, text+image input).

**Endpoint choice: Ollama's native `/api/chat`, not the OpenAI-compatible
`/v1/chat/completions`.** Both are real, already-supported Ollama
endpoints. This module uses the native one because Ollama's `format`
field on `/api/chat` accepts a full JSON Schema object (not just the
OpenAI-style `{"type": "json_object"}` free-form-JSON toggle) --
Ollama's own grammar-constrained decoder then guarantees the emitted
JSON structurally conforms to that schema (required keys present,
correct types/enums) rather than merely being *some* parseable JSON. For
turning a chat response into the exact `PlanOutput`/`DiffOutput`
dataclass shape this deliverable needs, that is materially cleaner
structured-output control than the OpenAI-compatible surface gives
today, at the cost of the client being Ollama-specific rather than
swappable for a generic OpenAI-compatible server -- an acceptable
trade-off here since the pinned model is served by Ollama specifically,
not behind a generic gateway.

**There is no live Ollama instance in this environment** (same
constraint `model_backend.py`'s docstring documents for D6 generally --
see that module first). This class is real, production-shaped client
code: real HTTP request/response construction, real JSON-Schema-
constrained decoding request, real parsing of the response into the
exact dataclasses `core.py` consumes, real retry-with-backoff on
transient connection failures, and a dedicated exception for "the model
endpoint is not ready yet" (the real deployment scales this backend's
GPU node to zero when idle, so a cold start -- connection refused while
the node is still booting, or a request timeout while the model is still
loading into VRAM -- is an expected, distinct condition, not a bug).
It is validated in this pass against a local mock Ollama HTTP server
(`tests/mock_ollama_server.py`), never against real model weights --
same "real code, mocked external boundary" discipline as every other
deliverable in this system.

Uses only the standard library's `urllib` for HTTP, matching
`services/source-control/src/source_control/github_client.py`'s idiom
(this package has no `httpx`/`requests` dependency in `pyproject.toml`,
and stdlib is sufficient for plain REST/JSON against a single host).
"""

from __future__ import annotations

import json
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

DEFAULT_MODEL = "ornith-1.5-35b-a3b"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class OllamaBackendError(Exception):
    """Base class for every error this module raises."""


class ModelNotReadyError(OllamaBackendError):
    """The endpoint could not be reached (connection refused) or did not
    respond within the configured timeout, after exhausting retries.

    Distinct from `OllamaRequestError` on purpose: the real deployment
    scales the GPU node this backend talks to down to zero when idle, so
    "still cold-starting" is an expected, retryable-by-the-caller
    condition a scheduler should back off and retry later, not a defect
    in the request itself."""


class OllamaRequestError(OllamaBackendError):
    """The endpoint responded, but with an HTTP error status (e.g. the
    model name is not pulled on that host, or a malformed request body).
    Not retried automatically -- retrying an unchanged bad request
    against a live server would just fail again."""

    def __init__(self, message: str, *, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class MalformedResponseError(OllamaBackendError):
    """The endpoint responded 200 OK, but the response body was not
    valid JSON, or was valid JSON that did not conform to the
    `PlanOutput`/`DiffOutput` shape this method requires (missing
    required key, wrong type). Raised instead of best-effort string
    scraping -- a caller must not silently receive a half-populated
    dataclass."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OllamaBackendConfig:
    """Plain constructor-arg config surface -- not read from environment
    variables inside `OllamaAgentBackend` (matches the config idiom used
    elsewhere in this repo, e.g. `tenant_cell.model_diversity`'s
    `TenantCellModelConfig` and `source_control.github_client`'s
    constructor args)."""

    base_url: str = "http://localhost:11434"
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 60.0
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0
    temperature: float = 0.0


# ---------------------------------------------------------------------------
# JSON Schemas passed as Ollama's `format` field -- constrains decoding,
# not just prompting, to the exact shape `_plan_from_dict`/`_diff_from_dict`
# below expect.
# ---------------------------------------------------------------------------

_ACCEPTANCE_CRITERION_SCHEMA = {
    "type": "object",
    "properties": {
        "criterion_id": {"type": "string"},
        "description": {"type": "string"},
        "verification_tests": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["criterion_id", "description", "verification_tests"],
}

_SUBTASK_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "description": {
            "type": "string",
            # Real live-run bug this closes (see tool_use_bedrock_backend.py's
            # module docstring): a plan authored a subtask literally titled
            # "Run tests to verify greet function works correctly". The
            # implementation agent has no tool that runs a test suite or a
            # shell command (only read_file/write_file/list_files/finish) --
            # every retry against that subtask spent its whole tool-calling
            # turn budget unable to make progress and never called finish,
            # eventually raising BedrockAgenticLoopExhaustedError. This
            # schema-level description is shared by every vendor backend
            # (Anthropic/OpenAI/Bedrock/Ollama all import PLAN_OUTPUT_SCHEMA
            # from this module), so fixing it here constrains plan authoring
            # for all of them at once, not just one vendor.
            "description": (
                "A single, concrete code-authoring action: create, modify, "
                "or delete specific real files. Never a subtask to run, "
                "execute, or verify tests, or to 'validate'/'confirm' the "
                "change works -- verification runs automatically, for "
                "real, against the actual files this run has touched, in "
                "a separate stage once every subtask here is implemented. "
                "The agent carrying out this subtask has no tool to "
                "execute a test suite or any shell command."
            ),
        },
        "parallel_group": {"type": ["string", "null"]},
        "depends_on": {"type": "array", "items": {"type": "string"}},
        "interface_contract": {"type": ["string", "null"]},
    },
    "required": ["task_id", "description", "parallel_group", "depends_on"],
}

PLAN_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "outcomes": {"type": "string"},
        "acceptance_criteria": {"type": "array", "items": _ACCEPTANCE_CRITERION_SCHEMA},
        # Real live-run bug this schema-level description closes: a plan
        # populated scope_in with prose feature descriptions ("Create a
        # single HTML file with embedded CSS and JavaScript") instead of
        # the real path ("tetris.html") the implementation loop actually
        # wrote to. `checkpoints.check_risk` does a literal, mechanical
        # set-difference between the diff's real touched files and this
        # list (Section 9.5's own design -- "never a judgment call left
        # to the implementing agent") -- so a prose scope_in made every
        # real file the run legitimately touched look "out of scope",
        # firing a real Section 9.3 risk checkpoint on the run's own
        # primary deliverable. Shared by every vendor backend (see the
        # _SUBTASK_SCHEMA comment above for why fixing it here covers
        # all of them at once).
        "scope_in": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Real, literal file paths (relative to the repo root) "
                "this plan expects to touch -- e.g. 'tetris.html', "
                "'src/app.py'. Never a prose description of a feature "
                "or task; every entry must be a path the implementation "
                "loop could plausibly pass to read_file/write_file."
            ),
        },
        "scope_out": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Real file paths, or a real path pattern, explicitly "
                "excluded from this plan's scope. Same constraint as "
                "scope_in: real paths, never prose."
            ),
        },
        "subtasks": {"type": "array", "items": _SUBTASK_SCHEMA},
        "story_size": {"type": "string", "enum": ["S", "M", "L", "XL"]},
        "cross_cutting_or_high_risk": {"type": "boolean"},
        "risk_tier": {"type": "string", "enum": ["low", "medium", "high"]},
        "rollback_strategy": {"type": "string"},
        "constraints": {"type": "string"},
        "prior_decisions": {"type": "string"},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "outcomes",
        "acceptance_criteria",
        "scope_in",
        "scope_out",
        "subtasks",
        "story_size",
        "cross_cutting_or_high_risk",
        "risk_tier",
        "rollback_strategy",
    ],
}

DIFF_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "files_touched": {"type": "array", "items": {"type": "string"}},
        "lines_changed": {"type": "integer"},
        "commit_message": {"type": "string"},
        "subtask_id": {"type": ["string", "null"]},
    },
    "required": ["files_touched", "lines_changed", "commit_message"],
}


_PLAN_SYSTEM_PROMPT = (
    "You are the planning stage of an autonomous coding agent. Respond "
    "with ONLY a single JSON object matching the required schema -- no "
    "prose, no markdown fences, no commentary before or after the JSON. "
    "Every subtask must be a concrete code-authoring action (create, "
    "modify, or delete specific real files) -- never a subtask to run, "
    "execute, or verify tests, since that happens automatically, for "
    "real, in a separate stage after every subtask here is implemented. "
    "scope_in and scope_out must be real, literal file paths (e.g. "
    "'tetris.html', 'src/app.py') -- never prose feature descriptions -- "
    "since the file(s) this plan's own deliverable requires must always "
    "be listed as real paths in scope_in."
)

_IMPLEMENT_SYSTEM_PROMPT = (
    "You are the implementation stage of an autonomous coding agent, "
    "reporting the diff you produced for one subtask. Respond with ONLY "
    "a single JSON object matching the required schema -- no prose, no "
    "markdown fences, no commentary before or after the JSON."
)


# ---------------------------------------------------------------------------
# Response -> dataclass translation
# ---------------------------------------------------------------------------


def _require(d: dict, key: str, *, context: str) -> Any:
    if key not in d:
        raise MalformedResponseError(f"{context}: missing required key {key!r} in {d!r}")
    return d[key]


def _plan_from_dict(d: dict) -> PlanOutput:
    if not isinstance(d, dict):
        raise MalformedResponseError(f"expected a JSON object for a plan, got {type(d).__name__}: {d!r}")
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
    except MalformedResponseError:
        raise
    except (TypeError, KeyError, AttributeError) as e:
        raise MalformedResponseError(f"plan response did not match PlanOutput shape: {e}") from e


def _diff_from_dict(d: dict) -> DiffOutput:
    if not isinstance(d, dict):
        raise MalformedResponseError(f"expected a JSON object for a diff, got {type(d).__name__}: {d!r}")
    try:
        return DiffOutput(
            files_touched=tuple(_require(d, "files_touched", context="diff")),
            lines_changed=int(_require(d, "lines_changed", context="diff")),
            commit_message=_require(d, "commit_message", context="diff"),
            subtask_id=d.get("subtask_id"),
        )
    except MalformedResponseError:
        raise
    except (TypeError, KeyError, AttributeError, ValueError) as e:
        raise MalformedResponseError(f"diff response did not match DiffOutput shape: {e}") from e


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class OllamaAgentBackend(AgentBackend):
    """Real `AgentBackend` client for a self-hosted Ollama instance. See
    module docstring for endpoint choice and what's real vs. mocked."""

    def __init__(self, config: OllamaBackendConfig | None = None):
        self._config = config or OllamaBackendConfig()

    # -- AgentBackend interface -------------------------------------------------

    def author_plan(self, *, run_context: dict) -> PlanOutput:
        user_prompt = (
            "Produce a plan for the following run context.\n\n"
            f"run_context:\n{json.dumps(run_context, default=str, indent=2)}"
        )
        response = self._chat(system=_PLAN_SYSTEM_PROMPT, user=user_prompt, schema=PLAN_OUTPUT_SCHEMA)
        return _plan_from_dict(response)

    def re_plan(self, *, run_context: dict, feedback: str) -> PlanOutput:
        user_prompt = (
            "Revise the plan for the following run context in light of the "
            "reviewer feedback below. Do not begin implementing the prior "
            "plan; produce a full revised plan.\n\n"
            f"run_context:\n{json.dumps(run_context, default=str, indent=2)}\n\n"
            f"feedback:\n{feedback}"
        )
        response = self._chat(system=_PLAN_SYSTEM_PROMPT, user=user_prompt, schema=PLAN_OUTPUT_SCHEMA)
        return _plan_from_dict(response)

    def implement_subtask(self, *, run_context: dict, subtask: SubTask) -> DiffOutput:
        user_prompt = (
            "Implement the following subtask and report the diff you produced.\n\n"
            f"run_context:\n{json.dumps(run_context, default=str, indent=2)}\n\n"
            "subtask:\n"
            f"{json.dumps({'task_id': subtask.task_id, 'description': subtask.description, 'parallel_group': subtask.parallel_group, 'depends_on': list(subtask.depends_on), 'interface_contract': subtask.interface_contract}, indent=2)}"
        )
        response = self._chat(system=_IMPLEMENT_SYSTEM_PROMPT, user=user_prompt, schema=DIFF_OUTPUT_SCHEMA)
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

    def _chat(self, *, system: str, user: str, schema: dict) -> dict:
        """POST /api/chat with a JSON-Schema-constrained `format`, retrying
        on transient connection failures, and return the parsed JSON
        object from `message.content`."""
        request_body = json.dumps(
            {
                "model": self._config.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "format": schema,
                "stream": False,
                "options": {"temperature": self._config.temperature},
            }
        ).encode("utf-8")

        url = f"{self._config.base_url.rstrip('/')}/api/chat"
        last_transient_error: Exception | None = None

        for attempt in range(self._config.max_retries):
            req = urllib.request.Request(
                url,
                data=request_body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=self._config.timeout_seconds) as resp:
                    raw_body = resp.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                raise OllamaRequestError(
                    f"Ollama endpoint returned HTTP {e.code}: {body}",
                    status_code=e.code,
                    body=body,
                ) from e
            except (urllib.error.URLError, ConnectionRefusedError, TimeoutError) as e:
                # URLError wraps connection-refused (node not up yet) and
                # socket.timeout wraps "accepted the connection but never
                # responded" (model still loading into VRAM) -- both are
                # the expected cold-start signature of a scaled-to-zero
                # GPU node, so both are retried the same way.
                last_transient_error = e
                if attempt < self._config.max_retries - 1:
                    time.sleep(self._config.retry_backoff_seconds * (2**attempt))
                    continue
                raise ModelNotReadyError(
                    f"Ollama endpoint at {self._config.base_url!r} did not respond after "
                    f"{self._config.max_retries} attempt(s) (model may still be cold-starting): {e}"
                ) from e
            else:
                break
        else:  # pragma: no cover - loop always returns/raises above
            raise ModelNotReadyError(
                f"Ollama endpoint at {self._config.base_url!r} did not respond: {last_transient_error}"
            )

        try:
            envelope = json.loads(raw_body)
        except json.JSONDecodeError as e:
            raise MalformedResponseError(f"response body was not valid JSON: {raw_body!r}") from e

        if not isinstance(envelope, dict) or "message" not in envelope or "content" not in envelope.get("message", {}):
            raise MalformedResponseError(f"response envelope missing message.content: {envelope!r}")

        content = envelope["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise MalformedResponseError(f"message.content was not valid JSON: {content!r}") from e
