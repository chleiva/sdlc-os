"""Config-driven `AgentBackend` selection (master spec Section 13.8,
"Multi-Vendor API-Key Inference", New Rev 9) -- the integration point
that ties `ollama_backend.py`, `anthropic_backend.py`, `openai_backend.py`,
and `bedrock_backend.py` together behind one call, so a deployment picks
its inference vendor via configuration rather than a code change at any
call site. Each of those four modules was built independently (as
separate, non-overlapping deliverable subtasks) against the same shared
`AgentBackend` interface (`model_backend.py`) -- this module is the one
place that actually imports all of them together and decides which one a
given deployment gets.

`core.py`'s `Orchestrator` never imports this module directly; it is
handed a constructed `AgentBackend` instance (`ScriptedAgentBackend` in
every existing test, per `model_backend.py`'s own docstring). This
factory is what a real deployment's own startup/wiring code calls instead
of hand-constructing one backend class -- see `services/orchestrator/README.md`'s
"Selecting an inference vendor" section for the human-facing version of
this same explanation.

**One vendor's config validation lives here too**: `BedrockBackendConfig.model_id`
has no default (see `bedrock_backend.py`'s module docstring for why --
the author of that module was not confident of MiniMax M2.5's exact
current Bedrock model-id string). This factory does not invent one
either; `vendor="bedrock"` without a `model_id` kwarg raises a clear
`ValueError` here rather than surfacing a confusing `TypeError` from deep
inside `BedrockBackendConfig`'s own constructor.
"""

from __future__ import annotations

from typing import Any

from orchestrator.anthropic_backend import AnthropicAgentBackend, AnthropicBackendConfig
from orchestrator.bedrock_backend import BedrockAgentBackend, BedrockBackendConfig
from orchestrator.model_backend import AgentBackend, ScriptedAgentBackend
from orchestrator.ollama_backend import OllamaAgentBackend, OllamaBackendConfig
from orchestrator.openai_backend import OpenAIAgentBackend, OpenAIBackendConfig

#: Every vendor this factory knows how to construct. Kept as a plain tuple
#: of strings (not an Enum) so a deployment's config file can name a
#: vendor with an ordinary string value, matching how every one of the
#: four vendor backends' own Config dataclasses are already plain,
#: serialization-friendly dataclasses rather than something requiring an
#: import to construct.
SUPPORTED_VENDORS = ("scripted", "ollama", "anthropic", "openai", "bedrock")


def create_agent_backend(vendor: str, **kwargs: Any) -> AgentBackend:
    """Construct the `AgentBackend` implementation named by `vendor`,
    passing `kwargs` through to that vendor's own Config dataclass (or, for
    `"scripted"`, straight to `ScriptedAgentBackend`'s own constructor).

    Every kwarg is the vendor backend's own config field name verbatim --
    this function does no renaming/translation, so a deployment's config
    file can be a near-literal transcription of whichever vendor's own
    `*BackendConfig` dataclass fields it is filling in. See each vendor
    module's own docstring for its exact fields, defaults, and what a
    missing/misconfigured field does (most raise a plain `TypeError` from
    the dataclass constructor itself for a genuinely missing required
    field -- Bedrock's `model_id` is the one case checked explicitly here,
    below, so the error message names the actual problem).

    Raises `ValueError` for an unrecognized vendor name, or a missing
    Bedrock `model_id` (see module docstring); otherwise lets each vendor
    Config dataclass's own constructor raise on a missing/invalid field.
    """
    if vendor == "scripted":
        return ScriptedAgentBackend(**kwargs)

    if vendor == "ollama":
        return OllamaAgentBackend(OllamaBackendConfig(**kwargs))

    if vendor == "anthropic":
        return AnthropicAgentBackend(AnthropicBackendConfig(**kwargs))

    if vendor == "openai":
        return OpenAIAgentBackend(OpenAIBackendConfig(**kwargs))

    if vendor == "bedrock":
        return _create_bedrock_backend(**kwargs)

    raise ValueError(f"unrecognized inference vendor {vendor!r}; supported vendors: {SUPPORTED_VENDORS}")


def _create_bedrock_backend(*, client: Any = None, **config_kwargs: Any) -> BedrockAgentBackend:
    """Bedrock is the one vendor whose backend takes a dependency-injected
    client object (see `bedrock_backend.py`'s module docstring: this
    module never constructs its own client so a real caller controls
    credential resolution and client-level `Config` entirely). If the
    caller doesn't supply one, this factory constructs a real
    `boto3.client("bedrock-runtime", region_name=...)` using the same
    `region_name` the rest of `config_kwargs` will also configure
    `BedrockBackendConfig` with -- real client construction, still no live
    AWS account reachable in this environment, same "real code, mocked
    external boundary" discipline as the module it configures.

    `model_id` has no default on `BedrockBackendConfig` (see that module's
    docstring) -- checked explicitly here so a deployment that forgets it
    gets a clear message naming the actual missing field, not a generic
    dataclass `TypeError`.
    """
    if "model_id" not in config_kwargs:
        raise ValueError(
            "vendor='bedrock' requires a 'model_id' kwarg -- BedrockBackendConfig has no "
            "default for it (see bedrock_backend.py's module docstring: the exact current "
            "Bedrock model-id string for MiniMax M2.5, or any other hosted model, is not "
            "something this repo can confirm without a live AWS account; supply the string "
            "your own Bedrock console/CLI reports, e.g. via `aws bedrock list-foundation-models`)."
        )

    config = BedrockBackendConfig(**config_kwargs)

    if client is None:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=config.region_name)

    return BedrockAgentBackend(config, client)
