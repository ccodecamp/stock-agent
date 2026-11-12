"""Shared setup for the browser tests: a real server serving web/, and a fake /api/chat.

The page and app.js are the real ones. Only the model-backed endpoint is stubbed, so
these tests cost nothing and never flake on API latency. Whether the real answers are
any good is the eval suite's job (evals/), not this one's.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest
import uvicorn

from src.api.app import app


def sse(*events: dict) -> str:
    """Build a Server-Sent Events body: one `data:` line per event, blank line between."""
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events)


def answer(text: str, *, tools: tuple[tuple[str, dict, bool], ...] = ()) -> str:
    """A normal successful turn: optional tool calls, then streamed text, then done."""
    events: list[dict] = []
    for name, tool_input, ok in tools:
        events.append({"type": "tool_call", "name": name, "input": tool_input})
        events.append({"type": "tool_result", "name": name, "ok": ok})
    mid = len(text) // 2
    events += [{"type": "text", "text": text[:mid]}, {"type": "text", "text": text[mid:]}]
    events.append({"type": "done", "messages": [{"role": "assistant", "content": [{"type": "text", "text": text}]}]})
    return sse(*events)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("test server did not start")
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def chat(page, base_url):
    """Open the app with /api/chat stubbed. Returns a controller to script replies and
    inspect the requests the page sent."""

    class Chat:
        requests: list[dict]

        def __init__(self):
            self.requests = []
            self.replies: list = []  # each: str body, or (status, str body)

        def reply(self, *items):
            self.replies = list(items)

    ctl = Chat()

    def handle(route):
        ctl.requests.append(json.loads(route.request.post_data))
        item = ctl.replies.pop(0) if len(ctl.replies) > 1 else ctl.replies[0]
        status, body = item if isinstance(item, tuple) else (200, item)
        content_type = "text/event-stream" if status == 200 else "application/json"
        route.fulfill(status=status, content_type=content_type, body=body)

    page.route("**/api/chat", handle)
    page.goto(base_url)
    return ctl
