"""Dual-channel identity, handle derivation, and token lifecycle.

The hub resolves the same profile from three different credential shapes
(``README.md`` → "Identity"). These tests pin each channel, the
precedence between them, and the auto-created-profile rule that means
there is never a signup step.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth_adapter import InvalidIdentityToken
from app.db import SessionLocal
from app.identity import handle_from_email, hash_token, unique_handle
from app.models.user import User

# ------------------------------------------------------------ handle rules


@pytest.mark.parametrize(
    "email, expected",
    [
        ("publisher@example.com", "publisher"),
        ("Publisher@example.com", "publisher"),
        ("first.last@example.com", "first-last"),
        ("a_b+c@example.com", "a-b-c"),
        ("--weird--@example.com", "weird"),
        ("...@example.com", "user"),
        ("", "user"),
    ],
)
def test_handle_from_email(email: str, expected: str) -> None:
    assert handle_from_email(email) == expected


def test_unique_handle_appends_a_digit_on_collision() -> None:
    with SessionLocal() as db:
        db.add(User(email="a@example.com", handle="sam"))
        db.commit()
        assert unique_handle(db, "sam") == "sam2"
        db.add(User(email="b@example.com", handle="sam2"))
        db.commit()
        assert unique_handle(db, "sam") == "sam3"


# --------------------------------------------------------- profile autocreate


def test_profile_is_created_on_first_authenticated_touch(auth_client: TestClient) -> None:
    """No signup step: the first request that carries an identity creates
    the row, with the handle derived from the email local-part."""
    body = auth_client.get("/api/me").json()
    assert body["profile"]["handle"] == "dev"
    assert body["profile"]["email"] == "dev@example.com"
    # Display name falls back to the handle until the user sets one.
    assert body["profile"]["display_name"] == "dev"
    assert body["tokens"] == []


def test_display_name_is_editable_and_clearable(auth_client: TestClient) -> None:
    patched = auth_client.patch("/api/me", json={"display_name": "  Dev User  "})
    assert patched.json()["profile"]["display_name"] == "Dev User"
    # An empty box means "clear it", which falls back rather than blanking.
    cleared = auth_client.patch("/api/me", json={"display_name": "   "})
    assert cleared.json()["profile"]["display_name"] == "dev"


def test_unauthenticated_requests_are_401(client: TestClient) -> None:
    for path in ("/api/me", "/api/bundles", "/api/profiles"):
        assert client.get(path).status_code == 401, path


# ------------------------------------------------------------ token channel


def _mint(auth_client: TestClient, name: str = "laptop") -> tuple[str, str]:
    resp = auth_client.post("/api/tokens", json={"name": name})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["token"], body["api_token"]["id"]


def test_minted_token_is_shown_once_and_stored_hashed(auth_client: TestClient) -> None:
    token, token_id = _mint(auth_client)
    assert token.startswith("phk_")
    assert len(token) == len("phk_") + 40

    listed = auth_client.get("/api/me").json()["tokens"]
    row = next(t for t in listed if t["id"] == token_id)
    # The list carries a display prefix, never the secret.
    assert row["prefix"] == token[:12]
    assert token not in str(listed)

    from app.models.token import ApiToken

    with SessionLocal() as db:
        stored = db.execute(select(ApiToken).where(ApiToken.id == token_id)).scalar_one()
        assert stored.token_hash == hash_token(token)
        assert token not in (stored.prefix + stored.name)


def test_token_channel_resolves_the_same_profile(
    client: TestClient, auth_client: TestClient
) -> None:
    token, _ = _mint(auth_client)
    resp = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["profile"]["handle"] == "dev"


def test_token_use_updates_last_used_at(client: TestClient, auth_client: TestClient) -> None:
    token, token_id = _mint(auth_client)
    assert auth_client.get("/api/me").json()["tokens"][0]["last_used_at"] is None
    client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    refreshed = auth_client.get("/api/me").json()["tokens"]
    assert next(t for t in refreshed if t["id"] == token_id)["last_used_at"] is not None


def test_revoked_token_is_rejected(client: TestClient, auth_client: TestClient) -> None:
    token, token_id = _mint(auth_client)
    assert auth_client.delete(f"/api/tokens/{token_id}").status_code == 200
    resp = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    # Revoking twice is a no-op, not an error.
    assert auth_client.delete(f"/api/tokens/{token_id}").status_code == 200


def test_unknown_token_is_401_not_a_jwt_error(client: TestClient) -> None:
    """A malformed ``phk_`` must be reported as a bad app token — it must
    not fall through to the identity-provider branch and 401 with a confusing
    'invalid identity token'."""
    resp = client.get("/api/me", headers={"Authorization": "Bearer phk_deadbeef"})
    assert resp.status_code == 401
    assert "token" in resp.json()["detail"].lower()


def test_cannot_revoke_someone_elses_token(
    auth_client: TestClient, other_client: TestClient
) -> None:
    _, token_id = _mint(auth_client)
    # 404, not 403 — whether a token id exists isn't a non-owner's business.
    assert other_client.delete(f"/api/tokens/{token_id}").status_code == 404


def test_app_token_wins_over_mcp_worker_headers(
    client: TestClient, auth_client: TestClient
) -> None:
    """Contract: "Bearer phk_ tokens also accepted and win if both present."

    The token is the more specific, app-issued credential, and a caller
    presenting one clearly meant to use it.
    """
    token, _ = _mint(auth_client)
    resp = client.get(
        "/api/me",
        headers={
            "Authorization": f"Bearer {token}",
            "X-MCP-User-Email": "someone-else@example.com",
        },
    )
    assert resp.json()["profile"]["handle"] == "dev"


def test_mcp_edge_headers_never_authenticate_rest_or_device(client: TestClient) -> None:
    """AUTH_TRUST_MCP_HEADERS permits only /mcp, not browser/device routes."""
    headers = {"X-MCP-User-Email": "edge-user@example.com"}
    assert client.get("/api/me", headers=headers).status_code == 401
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.get("/api/auth/device/pending?code=ABCDEFGH", headers=headers).status_code == 401
    for action in ("approve", "deny"):
        assert (
            client.post(
                f"/api/auth/device/{action}", json={"user_code": "ABCDEFGH"}, headers=headers
            ).status_code
            == 401
        )
    with SessionLocal() as db:
        assert db.execute(select(User).where(User.email == "edge-user@example.com")).first() is None


def test_org_profile_cannot_sign_in(client: TestClient) -> None:
    """The seeded ORG_EMAIL can never become an interactive user."""
    with SessionLocal() as db:
        org = db.execute(select(User).where(User.email == "hub-org@example.com")).scalar_one()
        assert org.is_org
        assert org.last_sign_in_at is None
    response = client.get("/api/me", headers={"X-Auth-Request-Email": "hub-org@example.com"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"
    with SessionLocal() as db:
        org = db.execute(select(User).where(User.email == "hub-org@example.com")).scalar_one()
        assert org.last_sign_in_at is None
        assert not org.is_admin


def test_org_profile_refused_by_identity_helper() -> None:
    with SessionLocal() as db:
        db.add(User(email="reserved@example.com", handle="reserved", is_org=True))
        db.commit()
        from app.identity import find_or_create_profile

        with pytest.raises(InvalidIdentityToken, match="Organization profiles cannot sign in"):
            find_or_create_profile(db, email="reserved@example.com", name="Reserved")
