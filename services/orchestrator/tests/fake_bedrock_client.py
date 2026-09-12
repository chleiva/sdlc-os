"""A hand-built fake `bedrock-runtime` client for
`test_bedrock_backend.py` -- not a test module itself (no `test_` prefix).

**Why a hand-built fake instead of `moto`.** `services/kms-boundary`
already uses `moto[kms]>=5.0` (installed in this repo at 5.2.3) to mock
real botocore KMS request/response shapes. This module checked that same
installed moto version's `bedrock-runtime` support first
(`moto/bedrockruntime/models.py`/`responses.py` in that package's venv)
and found it implements only the `invoke_model` action -- as an empty
stub (`inference_result: dict = {}`) at that -- with no `converse` or
`converse_stream` action registered at all. Since the whole point of
this backend is Bedrock's `Converse` API specifically (see
`bedrock_backend.py`'s module docstring for why: it is Bedrock's
model-agnostic structured-output mechanism via `toolConfig`), moto
cannot stand in for it at the installed version, so this module is a
plain Python object implementing just the one method
(`converse(**kwargs) -> dict`) `BedrockAgentBackend` calls, with queued
canned responses/exceptions and a call log -- the documented fallback
path for exactly this situation.
"""

from __future__ import annotations

import time
from typing import Any


class FakeBedrockRuntimeClient:
    """Stands in for a real `boto3` `bedrock-runtime` client. Tests queue
    responses (plain dicts, in the real Converse API response shape) or
    exceptions (e.g. `botocore.exceptions.ClientError`,
    `botocore.exceptions.EndpointConnectionError`) via `queue_response`/
    `queue_delay`, consumed in call order by `converse`."""

    def __init__(self) -> None:
        self.call_log: list[dict[str, Any]] = []
        self._responses: list[Any] = []
        self._delays: list[float] = []

    def queue_response(self, response_or_exception: Any) -> None:
        self._responses.append(response_or_exception)

    def queue_delay(self, seconds: float) -> None:
        """Queue a `time.sleep(seconds)` before the *next* queued
        response/exception is returned/raised -- used to exercise
        `BedrockAgentBackend`'s soft call timeout."""
        self._delays.append(seconds)

    def converse(self, **kwargs: Any) -> dict:
        self.call_log.append(kwargs)
        delay = self._delays.pop(0) if self._delays else 0.0
        if delay:
            time.sleep(delay)
        if not self._responses:
            raise AssertionError("FakeBedrockRuntimeClient.converse called with no queued response")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def converse_response_with_tool_call(tool_name: str, tool_input: dict) -> dict:
    """Build a real-shaped Bedrock `Converse` API success response whose
    single content block is a `toolUse` call to `tool_name` with
    `tool_input` as its `input`."""
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": "fake-tool-use-id",
                            "name": tool_name,
                            "input": tool_input,
                        }
                    }
                ],
            }
        },
        "stopReason": "tool_use",
        "usage": {"inputTokens": 100, "outputTokens": 50, "totalTokens": 150},
    }
