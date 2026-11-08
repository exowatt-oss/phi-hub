"""The optional MCP scaffold must be wireable end-to-end.

This guards the "Task group is not initialized. Make sure to use run()" bug:
a FastMCP ``streamable_http_app()`` mounted under FastAPI does NOT get its
lifespan run by the parent app, so the session manager's task group is never
started and *every* ``/mcp`` request 500s. The fix is two-fold and both halves
are exercised here, exactly as ``app/mcp_server.py``'s docstring
prescribe:

1. ``build_mcp_app()`` is a factory (fresh FastMCP per instance), not a
   module-level singleton — so ``StreamableHTTPSessionManager.run()`` is valid
   once per app and the test suite can build a fresh app per test.
2. The parent app's lifespan nests ``app.state.mcp_app.router.lifespan_context``
   so the mounted sub-app's lifespan actually runs.

The MCP surface is OFF by default in this template (``main.py`` doesn't import
``mcp_server``), so this test builds a minimal app that wires it up the way the
scaffold instructions say to. If the optional ``[mcp]`` extra isn't installed,
the test skips. ``build_transport_security()`` allows the ``testserver`` host by
default, so the DNS-rebinding guard doesn't reject the TestClient request.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

mcp_server = pytest.importorskip(
    "app.mcp_server",
    reason="MCP scaffold needs the MCP dependency installed",
)

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}


@pytest.fixture(autouse=True)
def _clean_db() -> None:
    """Override conftest's autouse ``_clean_db`` for this module.

    A same-named fixture in a more local scope replaces the conftest one for
    tests here. The MCP surface doesn't touch Postgres, so these tests must
    run even when a local DB isn't up (conftest's ``_clean_db`` skips on no-DB).
    """
    return None


def _make_app() -> FastAPI:
    """Build a minimal app wired exactly as the scaffold instructions say."""
    mcp_app = mcp_server.build_mcp_app()

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        # Pass the SUB-APP (the app that owns the router), not the parent
        # ``app`` — matches the scaffold instructions and is the idiomatic
        # Starlette form.
        sub = app.state.mcp_app
        async with sub.router.lifespan_context(sub):
            yield

    app = FastAPI(lifespan=_lifespan)
    app.state.mcp_app = mcp_app
    app.mount("/mcp", mcp_app)
    return app


def _data_lines(sse_text: str) -> list[str]:
    """Extract the ``data:`` payloads from an SSE ``text/event-stream`` body."""
    return [
        line[len("data:") :].strip() for line in sse_text.splitlines() if line.startswith("data:")
    ]


def _assert_successful_handshake(resp) -> None:
    # The core regression: an un-run session manager 500s with
    # "Task group is not initialized" — a successful handshake is 200.
    assert resp.status_code == 200, resp.text
    assert "task group is not initialized" not in resp.text.lower()
    # The handshake response is SSE. A JSON-RPC error would also be HTTP 200,
    # so assert a real ``result`` is present and no ``error`` — not just that
    # the request didn't 500.
    payloads = _data_lines(resp.text)
    assert payloads, f"no SSE data events in response: {resp.text!r}"
    assert any('"result"' in p for p in payloads), resp.text
    assert not any('"error"' in p for p in payloads), resp.text


def test_mcp_initialize_handshake() -> None:
    # ``with TestClient(...)`` enters the app lifespan, which (via the nested
    # context above) starts the MCP session manager's task group.
    with TestClient(_make_app()) as client:
        resp = client.post(
            "/mcp/",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json=_INITIALIZE,
        )
    _assert_successful_handshake(resp)


def test_build_mcp_app_is_a_fresh_instance_each_call() -> None:
    # Two fresh apps must each be independently runnable — the second
    # TestClient must not 500 with "run() can only be called once". This is
    # the regression guard for the module-singleton footgun.
    for _ in range(2):
        with TestClient(_make_app()) as client:
            resp = client.post(
                "/mcp/",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                json=_INITIALIZE,
            )
        _assert_successful_handshake(resp)
