"""A local HTTP server that mimics Ollama's native `/api/chat` response
shape closely enough to exercise `orchestrator.ollama_backend` end-to-end,
with no network access and no real Ollama instance or model weights --
same technique as `services/source-control/tests/mock_github_server.py`
(a real stdlib `http.server`, not a fake/simplified protocol).

Tests drive three scenarios through `MockOllamaState`:
  * a well-formed response: `state.next_content` is set to a JSON string
    (an encoded `PlanOutput`/`DiffOutput`-shaped dict) and served back as
    `message.content`, wrapped in Ollama's real chat-response envelope.
  * a malformed response: `state.next_content` is set to a non-JSON
    string (or `state.next_envelope_broken = True` to omit `message`
    entirely) -- served with a 200 status, exactly as a real Ollama
    instance would if the model ignored the `format` constraint or the
    caller pointed at a non-JSON-capable model.
  * a cold-start / not-ready scenario -- this server does not fake it
    with a special status code (a scaled-to-zero node does not answer at
    all): tests instead point `OllamaAgentBackend` at a closed port, or
    at `MockOllamaServer.stop()`, for real `ConnectionRefusedError`
    behavior, or use `state.delay_seconds` to hold the response open past
    the client's configured timeout for a real socket-timeout.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class MockOllamaState:
    next_content: str = "{}"  # raw string served as message.content
    envelope_broken: bool = False  # omit "message" from the envelope entirely
    raw_body_broken: bool = False  # serve a non-JSON HTTP body outright
    delay_seconds: float = 0.0  # sleep this long before responding (simulate a cold-starting model)
    status_code: int = 200
    model_name: str = "ornith-1.5-35b-a3b"
    call_log: list = None  # list[dict] of received request bodies

    def __post_init__(self):
        if self.call_log is None:
            self.call_log = []


class MockOllamaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - silence default stderr logging
        pass

    @property
    def state(self) -> MockOllamaState:
        return self.server.state  # type: ignore[attr-defined]

    def do_POST(self):
        if self.path != "/api/chat":
            return self._json(404, {"error": "not found"})

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"_unparsed": raw.decode("utf-8", errors="replace")}
        st = self.state
        st.call_log.append(body)

        if st.delay_seconds:
            time.sleep(st.delay_seconds)

        if st.status_code != 200:
            return self._json(st.status_code, {"error": "mock upstream error"})

        if st.raw_body_broken:
            return self._raw(200, b"not json at all {{{")

        envelope = {
            "model": st.model_name,
            "created_at": "2026-01-01T00:00:00Z",
            "done": True,
        }
        if not st.envelope_broken:
            envelope["message"] = {"role": "assistant", "content": st.next_content}

        return self._json(200, envelope)

    def _json(self, status: int, payload: dict) -> None:
        self._raw(status, json.dumps(payload).encode("utf-8"))

    def _raw(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MockOllamaServer:
    def __init__(self):
        self.state = MockOllamaState()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), MockOllamaHandler)
        self._httpd.state = self.state  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> str:
        self._thread.start()
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
