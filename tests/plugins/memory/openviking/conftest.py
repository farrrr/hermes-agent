"""Pytest fixtures for OpenViking memory provider tests.

Provides httpx.MockTransport-based fixtures that simulate the OpenViking REST
API without requiring a real server. Designed for use in T0a test infrastructure
(no behavior tests here — subsequent tasks add those).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List
from unittest.mock import patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Sample response payloads
# ---------------------------------------------------------------------------

_SAMPLE_SEARCH_RESULT = {
    "result": {
        "memories": [
            {
                "uri": "viking://memories/test-memory-001",
                "score": 0.95,
                "abstract": "Test memory about project setup",
            }
        ],
        "resources": [
            {
                "uri": "viking://resources/test-resource-001",
                "score": 0.87,
                "abstract": "Test resource: project README",
            }
        ],
        "skills": [
            {
                "uri": "viking://skills/test-skill-001",
                "score": 0.72,
                "abstract": "Test skill: code review checklist",
            }
        ],
        "total": 3,
    }
}

_SAMPLE_SESSION_CONTEXT = {
    "latest_archive_overview": "This session covers project setup and initial configuration.",
    "pre_archive_abstracts": [
        "Earlier discussion about dependency management.",
        "Overview of test infrastructure decisions.",
    ],
    "messages": [
        {"role": "user", "content": "How do I set up the project?"},
        {"role": "assistant", "content": "Run pip install -e '.[dev]' to install dev deps."},
    ],
    "estimatedTokens": 100,
    "stats": {
        "totalMessages": 2,
        "archivedMessages": 0,
        "sessionAge": "PT5M",
    },
}

_SAMPLE_FS_LS = {
    "result": [
        {
            "uri": "viking://memories/",
            "rel_path": "memories/",
            "name": "memories",
            "isDir": True,
            "abstract": "Long-term memories",
        },
        {
            "uri": "viking://resources/",
            "rel_path": "resources/",
            "name": "resources",
            "isDir": True,
            "abstract": "Indexed resources",
        },
    ]
}

_SAMPLE_FS_TREE = {
    "result": [
        {"uri": "viking://", "rel_path": "/", "isDir": True},
        {"uri": "viking://memories/", "rel_path": "memories/", "isDir": True},
        {"uri": "viking://resources/", "rel_path": "resources/", "isDir": True},
    ]
}

_SAMPLE_FS_STAT = {
    "result": {
        "uri": "viking://memories/test-memory-001",
        "isDir": False,
        "size": 512,
        "mtime": "2026-04-26T00:00:00Z",
    }
}


# ---------------------------------------------------------------------------
# MockTransport handler
# ---------------------------------------------------------------------------

def _build_response(status_code: int, body: Any) -> httpx.Response:
    """Build an httpx.Response with JSON body."""
    content = json.dumps(body).encode()
    return httpx.Response(
        status_code=status_code,
        headers={"content-type": "application/json"},
        content=content,
    )


class _OVMockTransport(httpx.BaseTransport):
    """httpx transport that simulates the OpenViking REST API endpoints.

    Also records every request for use in test assertions via the
    ``requests`` attribute.
    """

    def __init__(self):
        self.requests: List[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        method = request.method
        path = request.url.path
        query = dict(request.url.params)

        # GET /health
        if method == "GET" and path == "/health":
            return _build_response(200, {"status": "ok"})

        # POST /api/v1/search/find
        if method == "POST" and path == "/api/v1/search/find":
            return _build_response(200, _SAMPLE_SEARCH_RESULT)

        # POST /api/v1/sessions/{id}/messages
        m = re.fullmatch(r"/api/v1/sessions/([^/]+)/messages", path)
        if method == "POST" and m:
            return _build_response(200, {"ok": True})

        # POST /api/v1/sessions/{id}/commit
        m = re.fullmatch(r"/api/v1/sessions/([^/]+)/commit", path)
        if method == "POST" and m:
            return _build_response(202, {"status": "accepted", "task_id": "test-task-001"})

        # GET /api/v1/tasks/{task_id}
        m = re.fullmatch(r"/api/v1/tasks/([^/]+)", path)
        if method == "GET" and m:
            task_id = m.group(1)
            return _build_response(200, {
                "status": "completed",
                "task_id": task_id,
                "result": {"extracted_memories": 3},
            })

        # DELETE /api/v1/fs  (uri= query param)
        if method == "DELETE" and path == "/api/v1/fs":
            return _build_response(200, {"ok": True})

        # GET /api/v1/sessions/{id}/context
        m = re.fullmatch(r"/api/v1/sessions/([^/]+)/context", path)
        if method == "GET" and m:
            return _build_response(200, _SAMPLE_SESSION_CONTEXT)

        # GET /api/v1/content/abstract|overview|read
        if method == "GET" and path in (
            "/api/v1/content/abstract",
            "/api/v1/content/overview",
            "/api/v1/content/read",
        ):
            level = path.rsplit("/", 1)[-1]
            return _build_response(200, {
                "result": f"Sample {level} content for uri={query.get('uri', '')}",
            })

        # GET /api/v1/fs/ls|tree|stat
        if method == "GET" and path == "/api/v1/fs/ls":
            return _build_response(200, _SAMPLE_FS_LS)

        if method == "GET" and path == "/api/v1/fs/tree":
            return _build_response(200, _SAMPLE_FS_TREE)

        if method == "GET" and path == "/api/v1/fs/stat":
            return _build_response(200, _SAMPLE_FS_STAT)

        # POST /api/v1/resources
        if method == "POST" and path == "/api/v1/resources":
            return _build_response(200, {"ok": True, "resource_id": "test-res-001"})

        # Unmatched — return 404
        return _build_response(404, {"error": f"No mock for {method} {path}"})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_ov_transport() -> _OVMockTransport:
    """Return an httpx.MockTransport-compatible transport simulating the OV server.

    Tests can inspect `mock_ov_transport.requests` to assert what HTTP calls
    the provider made.
    """
    return _OVMockTransport()


@pytest.fixture()
def recorded_requests(mock_ov_transport: _OVMockTransport) -> List[httpx.Request]:
    """Convenience handle to the request log collected by mock_ov_transport."""
    return mock_ov_transport.requests


@pytest.fixture()
def ov_provider(mock_ov_transport: _OVMockTransport, monkeypatch):
    """Return an OpenVikingMemoryProvider wired to the mock transport.

    The provider is initialized with a test session_id. The mock transport is
    injected by patching httpx.Client so that all requests from _VikingClient
    go through the mock instead of a real network socket.

    Strategy: monkeypatch httpx.get / httpx.post at module level so that the
    top-level convenience calls in _VikingClient (which call httpx.get /
    httpx.post directly) route through the mock transport.
    """
    from plugins.memory.openviking import OpenVikingMemoryProvider

    transport = mock_ov_transport

    def _patched_get(url, **kwargs):
        request = httpx.Request("GET", url, headers=kwargs.get("headers", {}),
                                params=kwargs.get("params"))
        return transport.handle_request(request)

    def _patched_post(url, **kwargs):
        body = kwargs.get("json") or {}
        content = json.dumps(body).encode()
        request = httpx.Request(
            "POST", url,
            headers=kwargs.get("headers", {}),
            content=content,
        )
        return transport.handle_request(request)

    monkeypatch.setattr("httpx.get", _patched_get)
    monkeypatch.setattr("httpx.post", _patched_post)

    # Also patch the httpx module reference inside the plugin so that calls
    # via self._httpx.get / self._httpx.post also go through the mock.
    import plugins.memory.openviking as ov_module
    import httpx as _httpx_real

    class _MockedHttpx:
        @staticmethod
        def get(url, **kwargs):
            return _patched_get(url, **kwargs)

        @staticmethod
        def post(url, **kwargs):
            return _patched_post(url, **kwargs)

    monkeypatch.setattr(ov_module, "_get_httpx", lambda: _MockedHttpx)

    monkeypatch.setenv("OPENVIKING_ENDPOINT", "http://mock-ov-server")
    monkeypatch.setenv("OPENVIKING_API_KEY", "test-key")
    monkeypatch.setenv("OPENVIKING_ACCOUNT", "test-account")
    monkeypatch.setenv("OPENVIKING_USER", "test-user")
    monkeypatch.setenv("OPENVIKING_AGENT", "test-agent")

    provider = OpenVikingMemoryProvider()

    # Directly create client with mock httpx to bypass health check network call
    from plugins.memory.openviking import _VikingClient
    client = _VikingClient.__new__(_VikingClient)
    client._endpoint = "http://mock-ov-server"
    client._api_key = "test-key"
    client._account = "test-account"
    client._user = "test-user"
    client._agent = "test-agent"
    client._httpx = _MockedHttpx

    provider._endpoint = "http://mock-ov-server"
    provider._api_key = "test-key"
    provider._account = "test-account"
    provider._user = "test-user"
    provider._agent = "test-agent"
    provider._session_id = "test-session-001"
    provider._turn_count = 0
    provider._client = client

    return provider
