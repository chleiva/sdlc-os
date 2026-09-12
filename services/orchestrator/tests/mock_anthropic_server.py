"""A local HTTP server that mimics Anthropic's Messages API
(`POST /v1/messages`) response shape closely enough to exercise
`orchestrator.anthropic_backend` end-to-end, with no network access and
no real Anthropic account or model -- same technique as
`tests/mock_ollama_server.py` (a real stdlib `http.server`, not a fake/
simplified protocol).

Tests drive scenarios through `MockAnthropicState`:
  * a well-formed tool-use response: `state.next_tool_input` is a dict
    (an encoded `PlanOutput`/`DiffOutput`-shaped payload), served back as
    the `input` of a `tool_use` content block whose `name` matches the
    request's forced `tool_choice`, wrapped in a real Messages API
    response envelope.
  * a malformed/non-conforming response: `state.raw_body_broken = True`
    (a non-JSON HTTP body outright), `state.tool_use_missing = True` (the
    model "declines" the forced tool and replies with only a `text`
    block), or `state.wrong_tool_name` set to a name that does not match
    what the request asked for -- all served with a 200 status, exactly
    as a real endpoint might if the model somehow did not honor the
    forced `tool_choice`.
  * an HTTP error status: `state.status_code` (e.g. 401 for an invalid
    key, 400 for a bad request) is returned immediately and every time
    -- not retried by the client, so `call_log` should show exactly one
    request. `state.transient_fail_count` instead fails that many
    requests with `state.transient_status` (429 by default) before
    reverting to a 200 success -- deterministic retry-then-succeed
    behavior without timing races.
  * a connection failure -- this server does not fake it with a status
    code (a real dropped connection does not answer at all): tests
    instead point `AnthropicAgentBackend` at a closed port, or at
    `MockAnthropicServer.stop()`, for a real `ConnectionRefusedError`, or
    use `state.delay_seconds` to hold the response open past the
    client's configured timeout for a real socket timeout.

`call_log` records each received request as `{"headers": {...},
"body": {...}}` so tests can assert on what was actually sent (the
forced `tool_choice`, the model id, and -- for the "API key never
leaks" acceptance criterion -- that the configured key really was sent
as `x-api-key`, distinct from asserting it never appears in any
exception text)."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class MockAnthropicState:
    next_tool_input: dict = field(default_factory=dict)  # served as the tool_use block's "input"
    tool_use_missing: bool = False  # respond with only a text block, no tool_use at all
    wrong_tool_name: str | None = None  # serve a tool_use block under this name instead
    raw_body_broken: bool = False  # serve a non-JSON HTTP body outright
    delay_seconds: float = 0.0  # sleep this long before responding (simulate a hang/timeout)
    status_code: int = 200  # returned on every request once transient_fail_count is exhausted
    error_type: str = "error"  # Anthropic's inner error.type field, for non-200 responses
    transient_fail_count: int = 0  # fail this many requests with transient_status, then succeed
    transient_status: int = 429
    model_name: str = "claude-sonnet-5"
    call_log: list = field(default_factory=list)  # list[{"headers": dict, "body": dict}]

    def error_body(self, status_code: int) -> bytes:
        error_type = {
            401: "authentication_error",
            403: "permission_error",
            404: "not_found_error",
            429: "rate_limit_error",
        }.get(status_code, self.error_type)
        return json.dumps(
            {
                "type": "error",
                "error": {"type": error_type, "message": "mock upstream error"},
                "request_id": "req_mock_00000000",
            }
        ).encode("utf-8")


class MockAnthropicHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - silence default stderr logging
        pass

    @property
    def state(self) -> MockAnthropicState:
        return self.server.state  # type: ignore[attr-defined]

    def do_POST(self):
        if self.path != "/v1/messages":
            return self._json(404, {"type": "error", "error": {"type": "not_found_error", "message": "not found"}})

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"_unparsed": raw.decode("utf-8", errors="replace")}

        st = self.state
        st.call_log.append(
            {
                "headers": {
                    "x-api-key": self.headers.get("x-api-key"),
                    "anthropic-version": self.headers.get("anthropic-version"),
                },
                "body": body,
            }
        )

        if st.delay_seconds:
            time.sleep(st.delay_seconds)

        if st.transient_fail_count > 0:
            st.transient_fail_count -= 1
            return self._raw(st.transient_status, st.error_body(st.transient_status))

        if st.status_code != 200:
            return self._raw(st.status_code, st.error_body(st.status_code))

        if st.raw_body_broken:
            return self._raw(200, b"not json at all {{{")

        requested_tool_name = ""
        tool_choice = body.get("tool_choice") if isinstance(body, dict) else None
        if isinstance(tool_choice, dict):
            requested_tool_name = tool_choice.get("name", "")

        if st.tool_use_missing:
            content = [{"type": "text", "text": "I will not call a tool."}]
        else:
            served_name = st.wrong_tool_name or requested_tool_name
            content = [
                {
                    "type": "tool_use",
                    "id": "toolu_mock0000000000000000000000",
                    "name": served_name,
                    "input": st.next_tool_input,
                }
            ]

        envelope = {
            "id": "msg_mock0000000000000000000000",
            "type": "message",
            "role": "assistant",
            "model": st.model_name,
            "content": content,
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        return self._json(200, envelope)

    def _json(self, status: int, payload: dict) -> None:
        self._raw(status, json.dumps(payload).encode("utf-8"))

    def _raw(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MockAnthropicServer:
    def __init__(self):
        self.state = MockAnthropicState()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), MockAnthropicHandler)
        self._httpd.state = self.state  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> str:
        self._thread.start()
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
