"""Device authorization — the whole lifecycle plus its security properties.

The flow is the only way a credential leaves the hub without a human
typing one, so these tests pin more than the happy path: the single-use
reveal, the SSO-only approval, and what an anonymous poller can learn.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.identity import USER_CODE_LENGTH
from app.models.token import DEVICE_APPROVED, DEVICE_DENIED, DEVICE_EXPIRED, DeviceAuth

_USER_CODE_ALPHABET = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


def _start(client: TestClient, name: str = "sample-laptop") -> dict:
    resp = client.post("/api/auth/device/start", json={"device_name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_start_is_unauthenticated_and_well_shaped(client: TestClient) -> None:
    """The CLI has no credential yet, so ``start`` must work anonymously."""
    body = _start(client)
    assert body["device_code"].startswith("phk_")
    assert len(body["user_code"]) == USER_CODE_LENGTH
    # A–Z2–7 only: no 0/O or 1/I to misread off a terminal.
    assert set(body["user_code"]) <= _USER_CODE_ALPHABET
    assert body["verify_url"].endswith(f"/connect?code={body['user_code']}")
    assert body["expires_in"] == 15 * 60
    assert body["interval"] >= 1


def test_full_lifecycle_start_approve_poll(client: TestClient, auth_client: TestClient) -> None:
    """pending → approved → token, exactly once."""
    started = _start(client)

    pending = client.post("/api/auth/device/poll", json={"device_code": started["device_code"]})
    assert pending.json() == {"status": "pending", "token": None, "profile": None}

    # The browser side sees the device name before deciding.
    shown = auth_client.get(f"/api/auth/device/pending?code={started['user_code']}")
    assert shown.status_code == 200
    assert shown.json()["device_name"] == "sample-laptop"
    assert shown.json()["status"] == "pending"

    approved = auth_client.post(
        "/api/auth/device/approve", json={"user_code": started["user_code"]}
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == DEVICE_APPROVED

    collected = client.post(
        "/api/auth/device/poll", json={"device_code": started["device_code"]}
    ).json()
    assert collected["status"] == DEVICE_APPROVED
    assert collected["token"].startswith("phk_")
    assert collected["profile"]["handle"] == "dev"

    # Single-use reveal: a replayed poll gets the status but no secret.
    replay = client.post(
        "/api/auth/device/poll", json={"device_code": started["device_code"]}
    ).json()
    assert replay["status"] == DEVICE_APPROVED
    assert replay["token"] is None


def test_minted_token_authenticates_the_api(client: TestClient, auth_client: TestClient) -> None:
    """The whole point: the token the flow yields is a working credential."""
    started = _start(client)
    auth_client.post("/api/auth/device/approve", json={"user_code": started["user_code"]})
    token = client.post(
        "/api/auth/device/poll", json={"device_code": started["device_code"]}
    ).json()["token"]

    # A separate anonymous client, presenting only the token.
    client.headers.pop("X-Auth-Request-Email", None)
    me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["profile"]["handle"] == "dev"
    # The token shows up in the owner's list, named after the device.
    assert any(t["name"] == "sample-laptop" for t in me.json()["tokens"])


def test_deny_is_terminal(client: TestClient, auth_client: TestClient) -> None:
    started = _start(client)
    denied = auth_client.post("/api/auth/device/deny", json={"user_code": started["user_code"]})
    assert denied.status_code == 200
    assert denied.json()["status"] == DEVICE_DENIED

    polled = client.post(
        "/api/auth/device/poll", json={"device_code": started["device_code"]}
    ).json()
    assert polled == {"status": DEVICE_DENIED, "token": None, "profile": None}

    # And a late approval can't resurrect it.
    late = auth_client.post("/api/auth/device/approve", json={"user_code": started["user_code"]})
    assert late.status_code == 409


def test_expired_request_cannot_be_approved(client: TestClient, auth_client: TestClient) -> None:
    """Expiry is evaluated on read, so an approval one second late is refused."""
    started = _start(client)
    with SessionLocal() as db:
        device = db.execute(
            select(DeviceAuth).where(DeviceAuth.user_code == started["user_code"])
        ).scalar_one()
        device.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    assert (
        auth_client.post(
            "/api/auth/device/approve", json={"user_code": started["user_code"]}
        ).status_code
        == 409
    )
    polled = client.post(
        "/api/auth/device/poll", json={"device_code": started["device_code"]}
    ).json()
    assert polled["status"] == DEVICE_EXPIRED


def test_unknown_device_code_polls_as_expired_not_404(client: TestClient) -> None:
    """An anonymous caller must not be able to distinguish "no such code"
    from "that one timed out" — otherwise poll is a live-code oracle."""
    body = client.post("/api/auth/device/poll", json={"device_code": "phk_" + "0" * 40}).json()
    assert body["status"] == DEVICE_EXPIRED


def test_user_code_entry_is_forgiving(client: TestClient, auth_client: TestClient) -> None:
    """Lower case, spaces and dashes all resolve to the same request —
    the user is retyping something they read off a terminal."""
    started = _start(client)
    code = started["user_code"]
    messy = f" {code[:4].lower()}-{code[4:].lower()} "
    assert (
        auth_client.post("/api/auth/device/approve", json={"user_code": messy}).status_code == 200
    )


def test_pending_and_approve_require_sso(client: TestClient, auth_client: TestClient) -> None:
    """An API token must NOT be able to approve a device request.

    If it could, a leaked token could mint its own replacements and
    revoking the original would achieve nothing.
    """
    started = _start(client)
    auth_client.post("/api/auth/device/approve", json={"user_code": started["user_code"]})
    token = client.post(
        "/api/auth/device/poll", json={"device_code": started["device_code"]}
    ).json()["token"]

    second = _start(client)
    client.headers.pop("X-Auth-Request-Email", None)
    forbidden = client.post(
        "/api/auth/device/approve",
        json={"user_code": second["user_code"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert forbidden.status_code == 403
    assert "browser" in forbidden.json()["detail"].lower()

    # And anonymously it's a plain 401.
    assert (
        client.post("/api/auth/device/approve", json={"user_code": second["user_code"]}).status_code
        == 401
    )


def test_pending_404s_on_a_bogus_code(auth_client: TestClient) -> None:
    assert auth_client.get("/api/auth/device/pending?code=ZZZZZZZZ").status_code == 404


@pytest.mark.parametrize("blank", ["", "   "])
def test_device_name_defaults_when_blank(
    client: TestClient, auth_client: TestClient, blank: str
) -> None:
    started = client.post("/api/auth/device/start", json={"device_name": blank}).json()
    shown = auth_client.get(f"/api/auth/device/pending?code={started['user_code']}").json()
    assert shown["device_name"] == "phi CLI"
