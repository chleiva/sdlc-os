"""`BedrockAgentBackend` -- a real `AgentBackend` implementation (see
`model_backend.py`) that speaks to Amazon Bedrock's `bedrock-runtime`
`Converse` API, per master-spec Section 13.8 ("Multi-Vendor API-Key
Inference", New Rev 9): one of three external, API-key-authenticated
vendor options (Anthropic's API, OpenAI's API, or Amazon Bedrock) the
Section 14.16 Docker Compose deployment mode substitutes for self-hosted
model serving (Sections 13.1-13.7) when there is no standing GPU to
serve the pinned reference model from. This module is the Bedrock
option specifically -- Bedrock hosts, among other models, MiniMax M2.5
per that section's own text.

**New runtime dependency: `boto3`.** This package (`services/orchestrator`)
previously had no AWS SDK dependency at all. `boto3` (and its transitive
dependency `botocore`) is added here because this is the first module in
`services/orchestrator` that talks to AWS. It is *not* a new library
cold to this repo: `services/kms-boundary/pyproject.toml` already
depends on `boto3>=1.34` for real KMS request/response shapes, so this
is an established, reasonable choice, just newly needed by this
package specifically. Per this task's scope restriction (new files
only, no edits to any existing file in `services/orchestrator/` --
`pyproject.toml` included, since a separate integration task reconciles
every sibling vendor backend's dependency additions into one place at
once to avoid concurrent edits to the same file), this module does
**not** add `boto3` to `services/orchestrator/pyproject.toml` itself.
That file needs a `"boto3>=1.34"` entry added to `dependencies` before
this module is usable outside a venv where `boto3` happens to already
be installed -- flagged here for the integration task, and confirmed
importable-and-tested in this pass by installing `boto3` directly into
the venv (see `tests/test_bedrock_backend.py`'s module docstring).

**Structured output mechanism: Bedrock `toolConfig`, not a vendor/model-
specific JSON mode.** The Converse API (`bedrock-runtime` client's
`converse`/`converse_stream` operations) is Bedrock's model-agnostic
request/response envelope: the same shape is used whether the
underlying model is MiniMax M2.5, an Anthropic Claude model, Amazon's
own Titan/Nova family, or any other Converse-API-supporting model
Bedrock hosts -- exactly what lets one `BedrockAgentBackend` implementation
serve "whichever model this deployment's Bedrock account has configured"
without a model-specific code path. Structured output is forced by
declaring a single tool in `toolConfig.tools` whose `inputSchema.json` is
the exact `PLAN_OUTPUT_SCHEMA`/`DIFF_OUTPUT_SCHEMA` JSON Schema objects
`ollama_backend.py` already defines -- imported here, not redefined, per
this task's instructions -- and forcing the model to call it via
`toolConfig.toolChoice = {"tool": {"name": ...}}`. Tool use / function
calling is a real, generally-available Converse API capability, not
specific to any one hosted model.

Unlike Ollama's native `/api/chat` `format` field (see
`ollama_backend.py`'s module docstring), Bedrock's `toolConfig` does not
itself guarantee token-level structural conformance to the declared
schema -- it constrains the model to invoke the named tool with *some*
input object, but a given model can still emit a tool-call input that is
missing required keys or has the wrong shape. This module therefore
still validates every field on the way into `PlanOutput`/`DiffOutput`
(`_plan_from_tool_input`/`_diff_from_tool_input` below) and raises
`BedrockMalformedOutputError` rather than trusting the tool-call input
verbatim -- the same "never a silently half-populated dataclass"
discipline `ollama_backend.py` follows, re-derived locally here (rather
than importing `ollama_backend`'s private translator functions) so this
module's exception types stay this vendor's own, per this task's
instruction to name them distinctly rather than copy another backend's
exact class names.

**Dependency-injected `boto3` client, never constructed internally.**
`BedrockAgentBackend.__init__` takes an already-constructed `bedrock-runtime`
client (or any object satisfying the same `.converse(**kwargs)` call
signature) as a required constructor argument, exactly so tests can
point it at a fake/mocked client instead of a real one, and so a real
caller controls credential resolution (instance profile, environment,
shared config, an assumed role, etc.) and any client-level `Config`
(retries, connect/read timeouts) entirely outside this module -- this
module never reads AWS credentials, environment variables, or
`~/.aws/config` itself.

**No live AWS account/Bedrock access in this environment.** This class
is real, production-shaped client code: a real Converse-API request
shape, real retry-with-backoff on Bedrock's own transient error codes
(`ThrottlingException`, `ServiceUnavailableException`,
`ModelTimeoutException`, `ModelNotReadyException`, `InternalServerException`)
and on connection-level `botocore.exceptions.BotoCoreError`s, a
dedicated non-retried exception for authentication/authorization
failures (`AccessDeniedException`, `UnrecognizedClientException`,
`ExpiredTokenException`), and a soft client-side call timeout (since the
injected client is already fully constructed, this module cannot
retroactively change its underlying HTTP timeout, so `timeout_seconds`
is enforced here via a single-worker `ThreadPoolExecutor.result(timeout=...)`
around the call instead). Validated in this pass against a hand-built
fake `bedrock-runtime` client, never against a real Bedrock endpoint or
real model weights -- see `tests/test_bedrock_backend.py`'s module
docstring for exactly why moto is not used here (moto is installed at
5.2.3 in this repo's sibling `kms-boundary` package and does define a
`bedrockruntime` backend, but that backend implements only
`invoke_model` as an empty stub, not `converse`/`converse_stream` at
all, as of that version).

**`model_id` has no default.** The author of this module is not
confident of the exact, current Bedrock model-id string for MiniMax
M2.5 (Bedrock model ids are vendor-namespaced strings such as
`"<provider>.<model-name>-v1:0"`, sometimes with a cross-region
inference-profile prefix like `"us."`, and are added/changed by AWS
independently of this repo). Guessing one and shipping it as a default
would silently fail at call time against a real account, or -- worse --
silently target the wrong model. `BedrockBackendConfig.model_id` is
therefore a required constructor argument with no default; the human
operator wiring up a real deployment supplies the exact string from
their own Bedrock console/CLI (`aws bedrock list-foundation-models`) at
configuration time, per this package's existing "real code, mocked
external boundary" + SETUP.md-style discipline for anything this
environment cannot itself verify against a live account.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from orchestrator.model_backend import (
    AcceptanceCriterion,
    AgentBackend,
    DiffOutput,
    PlanOutput,
    SubTask,
)
from orchestrator.checkpoints import size_budget_prompt_text
from orchestrator.ollama_backend import DIFF_OUTPUT_SCHEMA, PLAN_OUTPUT_SCHEMA

# See checkpoints.size_budget_prompt_text's own comment (and
# ollama_backend.py's identical constant) for the real live-run bug this
# closes -- generated from the same DEFAULT_BUDGETS check_size enforces,
# spliced into every vendor's plan prompt in lockstep, never hand-copied.
_SIZE_BUDGET_GUIDANCE = size_budget_prompt_text()

logger = logging.getLogger(__name__)

_PLAN_TOOL_NAME = "emit_plan"
_DIFF_TOOL_NAME = "emit_diff"

_PLAN_TOOL_DESCRIPTION = (
    "Emit the structured plan for this run. Call this tool exactly once, "
    "with an input object that matches the declared schema in full -- no "
    "prose response, no partial plan."
)
_IMPLEMENT_TOOL_DESCRIPTION = (
    "Emit the diff produced for this subtask. Call this tool exactly "
    "once, with an input object that matches the declared schema in full."
)

_PLAN_SYSTEM_PROMPT = (
    "You are the planning stage of an autonomous coding agent. Produce "
    "your plan ONLY by calling the provided tool with a fully-populated "
    "input object -- never as free-text prose. Every subtask must be a "
    "concrete code-authoring action (create, modify, or delete specific "
    "real files) -- never a subtask to run, execute, or verify tests, "
    "since that happens automatically, for real, in a separate stage "
    "after every subtask here is implemented. scope_in and scope_out "
    "must be real, literal file paths (e.g. 'tetris.html', "
    "'src/app.py') -- never prose feature descriptions -- since the "
    "file(s) this plan's own deliverable requires must always be listed "
    "as real paths in scope_in.\n\n" + _SIZE_BUDGET_GUIDANCE
)
_IMPLEMENT_SYSTEM_PROMPT = (
    "You are the implementation stage of an autonomous coding agent, "
    "reporting the diff you produced for one subtask. Report it ONLY by "
    "calling the provided tool with a fully-populated input object -- "
    "never as free-text prose."
)

# Bedrock Converse error codes (surfaced via `botocore.exceptions.ClientError`'s
# `response["Error"]["Code"]`) that indicate a transient condition worth
# retrying with backoff, same spirit as `ollama_backend.ModelNotReadyError`'s
# cold-start handling -- these are the server telling the caller "not now",
# not "this request is wrong".
_THROTTLING_ERROR_CODES = frozenset(
    {
        "ThrottlingException",
        "TooManyRequestsException",
        "ServiceUnavailableException",
        "ModelTimeoutException",
        "ModelNotReadyException",
        "InternalServerException",
    }
)

# Error codes that mean the caller's credentials/permissions are the
# problem, never fixed by retrying the identical request.
_ACCESS_DENIED_ERROR_CODES = frozenset(
    {
        "AccessDeniedException",
        "UnrecognizedClientException",
        "ExpiredTokenException",
    }
)


# ---------------------------------------------------------------------------
# Exceptions -- named for this vendor specifically (Bedrock), not a copy of
# `ollama_backend.py`'s (or any sibling vendor backend's) exact class names.
# ---------------------------------------------------------------------------


class BedrockBackendError(Exception):
    """Base class for every error this module raises."""


class BedrockThrottledError(BedrockBackendError):
    """The request was retried `max_retries` times against a transient
    condition -- Bedrock throttling/service-unavailable/model-not-ready
    error codes, a connection-level `botocore.exceptions.BotoCoreError`,
    or this module's own soft call timeout -- and never succeeded.

    Distinct from `BedrockInvocationError` on purpose: a scheduler should
    back off and retry a *later* call, not treat this as a defect in the
    request itself."""


class BedrockAccessDeniedError(BedrockBackendError):
    """Bedrock rejected the request on authentication/authorization
    grounds (`AccessDeniedException`, `UnrecognizedClientException`,
    `ExpiredTokenException`). Never retried automatically: retrying an
    identical request with the same credentials/permissions will not
    change the outcome. The exception message carries only the error
    code, the model id, and the region -- never any credential material,
    even though botocore itself does not surface request-signing
    material (access keys, session tokens, signatures) on this exception
    in the first place, since this module never handles those directly
    (the `boto3` client is constructed and credentialed entirely by the
    caller -- see module docstring)."""


class BedrockInvocationError(BedrockBackendError):
    """Bedrock responded with a `ClientError` that is neither a
    throttling/transient code nor an access-denied code (e.g.
    `ValidationException`, `ResourceNotFoundException`,
    `ModelErrorException`) -- a problem with this specific request, not
    retried automatically."""

    def __init__(self, message: str, *, error_code: str | None = None, http_status_code: int | None = None):
        super().__init__(message)
        self.error_code = error_code
        self.http_status_code = http_status_code


class BedrockMalformedOutputError(BedrockBackendError):
    """Bedrock returned a 200-equivalent successful `converse` response,
    but either no `toolUse` content block named for the tool this call
    required was present, or its `input` did not conform to the
    `PlanOutput`/`DiffOutput` shape this method requires (missing
    required key, wrong type). Raised instead of best-effort scraping --
    a caller must not silently receive a half-populated dataclass."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BedrockBackendConfig:
    """Plain constructor-arg config surface -- matches the config idiom
    `ollama_backend.OllamaBackendConfig` already uses in this package
    (not read from environment variables inside `BedrockAgentBackend`).

    `model_id` has no default -- see module docstring for why: the
    author of this module is not confident of the exact, current Bedrock
    model-id string for MiniMax M2.5, so this is a required argument the
    operator supplies from their own Bedrock account/console rather than
    a guessed constant."""

    model_id: str
    region_name: str = "us-east-1"
    # Real live-run finding: 4096 output tokens is tight for a single
    # large-file generation (e.g. one HTML+CSS+JS file implementing a
    # full game) -- raised to 8192, still bounded, not unlimited.
    max_tokens: int = 8192
    # Real live-run finding: a real Bedrock read timeout (60s, the old
    # default) genuinely happens on a large/slow generation -- this
    # alone isn't the resilience fix (retrying matters far more, see
    # `call_converse_with_retry`), but a too-short timeout means even
    # the FIRST attempt of a legitimately-slow-but-healthy call gets
    # needlessly counted as a failure. Raised to 180s.
    timeout_seconds: float = 180.0
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0
    temperature: float = 0.0


# ---------------------------------------------------------------------------
# Response -> dataclass translation (local, not imported from
# `ollama_backend`, so this module's malformed-output exception stays its
# own type -- only the JSON schemas themselves are reused via import, per
# this task's instructions).
# ---------------------------------------------------------------------------


def _require(d: dict, key: str, *, context: str) -> Any:
    if key not in d:
        raise BedrockMalformedOutputError(f"{context}: missing required key {key!r} in {d!r}")
    return d[key]


def _plan_from_tool_input(d: dict) -> PlanOutput:
    if not isinstance(d, dict):
        raise BedrockMalformedOutputError(f"expected a JSON object for a plan tool-call input, got {type(d).__name__}: {d!r}")
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
    except BedrockMalformedOutputError:
        raise
    except (TypeError, KeyError, AttributeError) as e:
        raise BedrockMalformedOutputError(f"plan tool-call input did not match PlanOutput shape: {e}") from e


def _diff_from_tool_input(d: dict) -> DiffOutput:
    if not isinstance(d, dict):
        raise BedrockMalformedOutputError(f"expected a JSON object for a diff tool-call input, got {type(d).__name__}: {d!r}")
    try:
        return DiffOutput(
            files_touched=tuple(_require(d, "files_touched", context="diff")),
            lines_changed=int(_require(d, "lines_changed", context="diff")),
            commit_message=_require(d, "commit_message", context="diff"),
            subtask_id=d.get("subtask_id"),
        )
    except BedrockMalformedOutputError:
        raise
    except (TypeError, KeyError, AttributeError, ValueError) as e:
        raise BedrockMalformedOutputError(f"diff tool-call input did not match DiffOutput shape: {e}") from e


# ---------------------------------------------------------------------------
# Shared retry-with-backoff plumbing -- real live-run bug this closes: the
# retry loop below originally lived only as a method on `BedrockAgentBackend`
# (this class's single-forced-tool-call `_converse`). `tool_use_bedrock_backend
# .BedrockToolUseAgentBackend`'s multi-turn `_converse_with_tools` -- the one
# that actually drives a real implementation loop -- called `self._client
# .converse(...)` directly with NO retry at all, so a single transient
# `ReadTimeoutError` (hit for real on a live run generating a large file)
# crashed the entire run outright instead of retrying. Extracted here as a
# free function so both call sites share identical resilience against the
# exact same real Bedrock failure modes, rather than one having it and the
# other not.
# ---------------------------------------------------------------------------


def _call_with_soft_timeout(client: Any, request_kwargs: dict, *, timeout_seconds: float) -> dict:
    """Enforce `timeout_seconds` at this module's level, since the
    injected `boto3` client is already fully constructed by the caller
    and this module has no way to retroactively change its underlying
    HTTP timeout. A single-worker executor is used purely as a stdlib-
    only timeout mechanism, not for concurrency.

    Deliberately *not* `with ThreadPoolExecutor(...) as pool: ...`: the
    context manager's `__exit__` calls `shutdown(wait=True)`, which
    would block until the slow/hung call actually finishes -- defeating
    the point of a soft timeout for a call that never returns in time.
    `shutdown(wait=False)` here lets this function return/raise as soon
    as `future.result(timeout=...)` does, while the abandoned background
    thread (if any) finishes or is eventually reclaimed on its own."""
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(client.converse, **request_kwargs)
        return future.result(timeout=timeout_seconds)
    finally:
        pool.shutdown(wait=False)


def _call_converse_with_retry_one_region(*, client: Any, request_kwargs: dict, config: BedrockBackendConfig) -> dict:
    """`call_converse_with_retry`'s real retry-with-backoff loop against
    exactly one region/client -- see that function for the public
    entrypoint, which wraps this with region fallback."""
    response: dict | None = None
    last_transient_summary: str | None = None

    for attempt in range(config.max_retries):
        try:
            response = _call_with_soft_timeout(client, request_kwargs, timeout_seconds=config.timeout_seconds)
        except concurrent.futures.TimeoutError as e:
            last_transient_summary = f"call timed out after {config.timeout_seconds}s"
            if attempt < config.max_retries - 1:
                logger.warning(
                    "Bedrock converse call to model %r timed out (attempt %d/%d); retrying",
                    config.model_id,
                    attempt + 1,
                    config.max_retries,
                )
                time.sleep(config.retry_backoff_seconds * (2**attempt))
                continue
            raise BedrockThrottledError(
                f"Bedrock converse call to model {config.model_id!r} in region "
                f"{config.region_name!r} did not complete after "
                f"{config.max_retries} attempt(s): {last_transient_summary}"
            ) from e
        except ClientError as e:
            error = e.response.get("Error", {}) if hasattr(e, "response") else {}
            code = error.get("Code", "")
            message = error.get("Message", "")
            http_status_code = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode") if hasattr(e, "response") else None

            if code in _ACCESS_DENIED_ERROR_CODES:
                # Never retried, and the message carries only the error
                # code/model id/region -- no credential material ever
                # passes through this module (see `BedrockAccessDeniedError`'s
                # docstring).
                raise BedrockAccessDeniedError(
                    f"Bedrock denied access for model {config.model_id!r} in region "
                    f"{config.region_name!r}: {code}: {message}"
                ) from e

            if code in _THROTTLING_ERROR_CODES:
                last_transient_summary = f"{code}: {message}"
                if attempt < config.max_retries - 1:
                    logger.warning(
                        "Bedrock converse call to model %r hit %s (attempt %d/%d); retrying",
                        config.model_id,
                        code,
                        attempt + 1,
                        config.max_retries,
                    )
                    time.sleep(config.retry_backoff_seconds * (2**attempt))
                    continue
                raise BedrockThrottledError(
                    f"Bedrock converse call to model {config.model_id!r} in region "
                    f"{config.region_name!r} did not succeed after "
                    f"{config.max_retries} attempt(s): {last_transient_summary}"
                ) from e

            # Any other ClientError (ValidationException,
            # ResourceNotFoundException, ModelErrorException, ...): a
            # problem with this specific request, not retried.
            raise BedrockInvocationError(
                f"Bedrock converse call to model {config.model_id!r} in region "
                f"{config.region_name!r} failed: {code}: {message}",
                error_code=code,
                http_status_code=http_status_code,
            ) from e
        except BotoCoreError as e:
            # Connection-level failure (endpoint unreachable, connect/
            # read timeout inside botocore itself) -- treated the same
            # as a throttling code: retryable, never a defect in the
            # request. The exception's own string form is botocore's,
            # not ours; it never includes credential material either
            # (endpoint URL and a generic message only).
            last_transient_summary = str(e)
            if attempt < config.max_retries - 1:
                logger.warning(
                    "Bedrock converse call to model %r hit a connection error (attempt %d/%d); retrying",
                    config.model_id,
                    attempt + 1,
                    config.max_retries,
                )
                time.sleep(config.retry_backoff_seconds * (2**attempt))
                continue
            raise BedrockThrottledError(
                f"Bedrock converse call to model {config.model_id!r} in region "
                f"{config.region_name!r} did not succeed after "
                f"{config.max_retries} attempt(s): connection error: {last_transient_summary}"
            ) from e
        else:
            break
    else:  # pragma: no cover - loop always returns/raises above
        raise BedrockThrottledError(
            f"Bedrock converse call to model {config.model_id!r} did not succeed: "
            f"{last_transient_summary}"
        )

    assert response is not None  # for type-checkers; unreachable otherwise
    return response


def call_converse_with_retry(
    *, client: Any, request_kwargs: dict, config: BedrockBackendConfig,
    fallback_clients: list[tuple[Any, str]] | None = None,
    sticky_state: dict | None = None,
) -> dict:
    """Call `bedrock-runtime`'s real `converse` operation against
    `client`/`config.region_name`, retrying with exponential backoff on
    a soft timeout, Bedrock's own throttling/transient error codes, or a
    connection-level `BotoCoreError` (including `ReadTimeoutError`/
    `ConnectTimeoutError` -- both real `BotoCoreError` subclasses).
    Never retries an access-denied code (retrying with the same
    credentials changes nothing) or any other `ClientError` (a defect
    in the request itself, not a transient condition).

    `fallback_clients` (New -- real live-run finding: a persistent
    regional Bedrock slowdown/outage exhausted every retry in one
    region on a real run): additional real `(client, region_name)`
    pairs, each a real `boto3.client("bedrock-runtime", region_name=...)`
    for a region that also serves this same model. If the primary
    region exhausts its own `max_retries` (a real `BedrockThrottledError`),
    this tries each fallback region in turn, each getting its own full
    retry budget, before finally giving up. Never falls over on
    `BedrockAccessDeniedError`/`BedrockInvocationError` -- those mean
    the request or credentials are the problem, not the region, and
    retrying the identical request against a different region would not
    change that.

    `sticky_state` (New): a small caller-owned `dict` (e.g.
    `{"index": 0}`, one per `BedrockAgentBackend`/
    `BedrockToolUseAgentBackend` instance, persisting across every real
    Converse call that instance makes) this function reads its starting
    region from and updates on success. Without this, a multi-turn
    session (the implementation loop) would retry the *dead* primary
    region's full budget again on every single turn even after a
    fallback region already proved to work -- wasteful (this session's
    real default: up to 180s x 3 wasted, every turn) and pointless once
    one real outage is already known. With it, a fallback that
    succeeded once becomes this instance's new preferred region for
    every subsequent call, and only falls back further (or back to the
    original) if that region itself later fails too.

    Returns the raw Converse API response dict -- callers extract
    whatever shape they need from it (a single forced tool call's
    input, or a multi-turn tool-use message)."""
    attempts: list[tuple[Any, BedrockBackendConfig]] = [(client, config)]
    for fallback_client, fallback_region in fallback_clients or []:
        attempts.append((fallback_client, dataclasses.replace(config, region_name=fallback_region)))

    start = (sticky_state.get("index", 0) % len(attempts)) if sticky_state is not None else 0
    order = list(range(start, len(attempts))) + list(range(start))

    last_error: BedrockThrottledError | None = None
    for position, index in enumerate(order):
        attempt_client, attempt_config = attempts[index]
        try:
            response = _call_converse_with_retry_one_region(client=attempt_client, request_kwargs=request_kwargs, config=attempt_config)
        except BedrockThrottledError as e:
            last_error = e
            if position < len(order) - 1:
                next_region = attempts[order[position + 1]][1].region_name
                logger.warning(
                    "Bedrock region %r exhausted its own retry budget; falling over to region %r",
                    attempt_config.region_name, next_region,
                )
            continue

        if sticky_state is not None:
            sticky_state["index"] = index
        return response

    assert last_error is not None  # for type-checkers; unreachable otherwise (attempts always has >=1 entry)
    raise last_error


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class BedrockAgentBackend(AgentBackend):
    """Real `AgentBackend` client for Amazon Bedrock's `bedrock-runtime`
    `Converse` API. See module docstring for what's real vs. mocked, the
    structured-output mechanism, and why `model_id` has no default."""

    def __init__(self, config: BedrockBackendConfig, client: Any, *, fallback_clients: list[tuple[Any, str]] | None = None) -> None:
        """`client` is a dependency-injected object implementing the same
        `.converse(**kwargs) -> dict` call signature as a real `boto3`
        `bedrock-runtime` client (constructed by the caller, e.g.
        `boto3.client("bedrock-runtime", region_name=config.region_name)`,
        with whatever credential source and client-level `Config` --
        connect/read timeouts, its own botocore-level retry policy -- the
        caller wants). This module never constructs its own client and
        never reads AWS credentials, environment variables, or shared
        config itself -- see module docstring.

        `fallback_clients` (New): additional real `(client, region_name)`
        pairs -- caller-constructed, same discipline as `client` above
        -- to fail over to, in order, if `client`/`config.region_name`
        exhausts its own retry budget. See `call_converse_with_retry`."""
        self._config = config
        self._client = client
        self._fallback_clients = fallback_clients or []
        # One instance, one sticky region preference -- see
        # `call_converse_with_retry`'s own docstring for why.
        self._sticky_state: dict = {"index": 0}

    # -- AgentBackend interface -------------------------------------------------

    def author_plan(self, *, run_context: dict) -> PlanOutput:
        user_prompt = (
            "Produce a plan for the following run context.\n\n"
            f"run_context:\n{json.dumps(run_context, default=str, indent=2)}"
        )
        tool_input = self._converse(
            system=_PLAN_SYSTEM_PROMPT,
            user=user_prompt,
            tool_name=_PLAN_TOOL_NAME,
            tool_description=_PLAN_TOOL_DESCRIPTION,
            schema=PLAN_OUTPUT_SCHEMA,
        )
        return _plan_from_tool_input(tool_input)

    def re_plan(self, *, run_context: dict, feedback: str) -> PlanOutput:
        user_prompt = (
            "Revise the plan for the following run context in light of "
            "the reviewer feedback below. Do not begin implementing the "
            "prior plan; produce a full revised plan.\n\n"
            f"run_context:\n{json.dumps(run_context, default=str, indent=2)}\n\n"
            f"feedback:\n{feedback}"
        )
        tool_input = self._converse(
            system=_PLAN_SYSTEM_PROMPT,
            user=user_prompt,
            tool_name=_PLAN_TOOL_NAME,
            tool_description=_PLAN_TOOL_DESCRIPTION,
            schema=PLAN_OUTPUT_SCHEMA,
        )
        return _plan_from_tool_input(tool_input)

    def implement_subtask(self, *, run_context: dict, subtask: SubTask) -> DiffOutput:
        user_prompt = (
            "Implement the following subtask and report the diff you "
            "produced.\n\n"
            f"run_context:\n{json.dumps(run_context, default=str, indent=2)}\n\n"
            "subtask:\n"
            f"{json.dumps({'task_id': subtask.task_id, 'description': subtask.description, 'parallel_group': subtask.parallel_group, 'depends_on': list(subtask.depends_on), 'interface_contract': subtask.interface_contract}, indent=2)}"
        )
        tool_input = self._converse(
            system=_IMPLEMENT_SYSTEM_PROMPT,
            user=user_prompt,
            tool_name=_DIFF_TOOL_NAME,
            tool_description=_IMPLEMENT_TOOL_DESCRIPTION,
            schema=DIFF_OUTPUT_SCHEMA,
        )
        diff = _diff_from_tool_input(tool_input)
        if diff.subtask_id is None:
            diff = DiffOutput(
                files_touched=diff.files_touched,
                lines_changed=diff.lines_changed,
                commit_message=diff.commit_message,
                subtask_id=subtask.task_id,
            )
        return diff

    # -- Converse plumbing ---------------------------------------------------

    def _converse(self, *, system: str, user: str, tool_name: str, tool_description: str, schema: dict) -> dict:
        """Call `converse` with a `toolConfig` that forces a single named
        tool call, retrying on transient error codes/connection failures/
        soft timeout (`call_converse_with_retry`, shared with
        `tool_use_bedrock_backend.py`'s multi-turn loop), then extract
        and return that tool call's `input` object (still unvalidated
        JSON at this point -- callers run it through
        `_plan_from_tool_input`/`_diff_from_tool_input`)."""
        request_kwargs = {
            "modelId": self._config.model_id,
            "system": [{"text": system}],
            "messages": [{"role": "user", "content": [{"text": user}]}],
            "inferenceConfig": {
                "maxTokens": self._config.max_tokens,
                "temperature": self._config.temperature,
            },
            "toolConfig": {
                "tools": [
                    {
                        "toolSpec": {
                            "name": tool_name,
                            "description": tool_description,
                            "inputSchema": {"json": schema},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": tool_name}},
            },
        }
        response = call_converse_with_retry(
            client=self._client, request_kwargs=request_kwargs, config=self._config,
            fallback_clients=self._fallback_clients, sticky_state=self._sticky_state,
        )
        return self._extract_tool_input(response, tool_name=tool_name)

    def _extract_tool_input(self, response: dict, *, tool_name: str) -> dict:
        try:
            content_blocks = response["output"]["message"]["content"]
        except (KeyError, TypeError) as e:
            raise BedrockMalformedOutputError(f"response missing output.message.content: {response!r}") from e

        if not isinstance(content_blocks, list):
            raise BedrockMalformedOutputError(f"output.message.content was not a list: {content_blocks!r}")

        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            tool_use = block.get("toolUse")
            if isinstance(tool_use, dict) and tool_use.get("name") == tool_name:
                tool_input = tool_use.get("input")
                if not isinstance(tool_input, dict):
                    raise BedrockMalformedOutputError(f"toolUse.input for tool {tool_name!r} was not a JSON object: {tool_input!r}")
                return tool_input

        raise BedrockMalformedOutputError(
            f"no toolUse content block named {tool_name!r} found in response content: {content_blocks!r}"
        )
