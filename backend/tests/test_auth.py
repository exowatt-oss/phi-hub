"""Auth API for a configurable self-hosted identity proxy."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_me_without_header_returns_401(client: TestClient) -> None:
    """A request without verified identity is unauthenticated."""
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_with_auth_header_returns_user(auth_client: TestClient) -> None:
    """Configured test-only forwarded identity resolves to a local user."""
    resp = auth_client.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "dev@example.com"
    assert body["is_admin"] is True
    # phi hub adds the public handle to the identity payload — the sidebar
    # pill links to /p/<handle>, so a missing one would break navigation.
    assert body["handle"] == "dev"


def test_logout_has_neutral_local_fallback(client: TestClient) -> None:
    resp = client.get("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["logout_url"] == "http://testserver/"


def test_preview_route_is_public(client: TestClient) -> None:
    """The catalog screenshotter hits ``/preview`` anonymously — the route
    must not be gated by ``get_current_user``."""
    resp = client.get("/preview")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
