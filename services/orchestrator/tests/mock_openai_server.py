"""A local HTTP server that mimics OpenAI's Chat Completions API
(`POST /v1/chat/completions`) response shape closely enough to exercise
`orchestrator.openai_backend` end-to-end, with no network access and no
real OpenAI account or model weights -- same technique as
`tests/mock_ollama_server.py` (a real stdlib `http.server`, not a
fake/simplified protocol).

Tests drive scenarios through `MockOpenAIState`:
  * a well-formed structured-output response: `state.next_content` is
    set to a JSON string (an encoded `PlanOutput`/`DiffOutput`-shaped
    dict) and served back as `choices[0].message.content`, wrapped in a
    real chat-completion envelope.
  * a malformed/non-conforming response: `state.next_content` set to a
    non-JSON string, `state.content_missing = True` (the model "refused"
    -- `message.content` is `null`), or `state.choices_broken = True`
    (the envelope omits `choices` entirely) -- all served with a 200
    status, exactly as the real API would if something upstream of the
    strict schema decoder still went wrong.
  * an HTTP error status: `state.status_code` set to e.g. 401 (invalid
    API key) or 429 (rate limited); `state.error_body` controls the
    (OpenAI-shaped) JSON error envelope served back.
  * a connection failure: tests point `OpenAIAgentBackend` at a closed
    port, or call `MockOpenAIServer.stop()`, for a real
    `ConnectionRefusedError`; `state.delay_seconds` holds the response
    open past the client's configured timeout for a real socket-timeout.

`state.received_auth_headers` records the raw `Authorization` header
value from every request received, so tests can assert the client sent
`Bearer <api_key>` -- without the mock server ever needing to (or being
able to) echo the key back in a response body.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class MockOpenAIState:
    next_content: str = "{}"  # raw string served as choices[0].message.content
    finish_reason: str = "stop"
    content_missing: bool = False  # message.content is null (simulated refusal)
    choices_broken: bool = False  # omit "choices" from the envelope entirely
    raw_body_broken: bool = False  # serve a non-JSON HTTP body outright
    delay_seconds: float = 0.0  # sleep this long before responding
    status_code: int = 200
    error_body: str = '{"error": {"message": "mock upstream error", "type": "mock_error", "code": null}}'
    model_name: str = "gpt-4.1"
    call_log: list = field(default_factory=list)  # list[dict] of received request bodies
    received_auth_headers: list = field(default_factory=list)  # list[str]
    flip_to_success_after_calls: int | None = None  # deterministic "clears on retry N" hook


class MockOpenAIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - silence default stderr logging
        pass

    @property
    def state(self) -> MockOpenAIState:
        return self.server.state  # type: ignore[attr-defined]

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self._json(404, {"error": "not found"})

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"_unparsed": raw.decode("utf-8", errors="replace")}

        st = self.state
        st.call_log.append(body)
        st.received_auth_headers.append(self.headers.get("Authorization", ""))

        if st.flip_to_success_after_calls is not None and len(st.call_log) >= st.flip_to_success_after_calls:
            # Deterministic "the transient condition cleared partway
            # through the retry budget" hook -- avoids a timing-based
            # race between a background thread and the client's retry
            # loop (see test_rate_limit_then_success_retries_...).
            st.status_code = 200

        if st.delay_seconds:
            time.sleep(st.delay_seconds)

        if st.status_code != 200:
            return self._raw(st.status_code, st.error_body.encode("utf-8"))

        if st.raw_body_broken:
            return self._raw(200, b"not json at all {{{")

        message: dict = {"role": "assistant"}
        if st.content_missing:
            message["content"] = None
            message["refusal"] = "mock refusal"
        else:
            message["content"] = st.next_content

        envelope = {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": 1735689600,
            "model": st.model_name,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": st.finish_reason,
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        if st.choices_broken:
            del envelope["choices"]

        return self._json(200, envelope)

    def _json(self, status: int, payload: dict) -> None:
        self._raw(status, json.dumps(payload).encode("utf-8"))

    def _raw(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MockOpenAIServer:
    def __init__(self):
        self.state = MockOpenAIState()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), MockOpenAIHandler)
        self._httpd.state = self.state  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> str:
        self._thread.start()
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
