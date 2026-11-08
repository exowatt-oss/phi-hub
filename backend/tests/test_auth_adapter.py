"""Fail-closed authentication boundaries independent of an IdP service."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth_adapter import InvalidIdentityToken, user_from_headers
from app.config import Settings


def test_default_auth_is_disabled_even_with_spoofed_identity_headers() -> None:
    settings = Settings(_env_file=None, auth_mode="disabled")
    assert user_from_headers({"X-Auth-Request-Email": "admin@example.com"}, settings) is None
    assert user_from_headers({"X-MCP-User-Email": "admin@example.com"}, settings) is None


def test_dev_mode_refused_in_production() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="prod", environment="production", auth_mode="dev")


@pytest.mark.parametrize("environment", ["staging", "production", "prod", "", "preview"])
def test_dev_mode_refused_outside_explicit_allowlist(environment: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="local", environment=environment, auth_mode="dev")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env=environment, environment="local", auth_mode="dev")


@pytest.mark.parametrize("environment", ["local", "dev", "development", "test", "DEV", "TeSt"])
def test_dev_mode_allowed_only_in_local_environments(environment: str) -> None:
    settings = Settings(
        _env_file=None, app_env=environment, environment=environment, auth_mode="dev"
    )
    assert user_from_headers({}, settings).email == "dev@example.org"


def test_invalid_forwarded_state_raises_without_assertions() -> None:
    settings = Settings(_env_file=None, auth_mode="disabled", auth_trust_forwarded_headers=False)
    # model_copy bypasses validators, as a mutated runtime configuration could.
    with pytest.raises(ValueError, match="Forwarded identity requires"):
        user_from_headers({}, settings.model_copy(update={"auth_mode": "forwarded"}))
    with pytest.raises(ValueError, match="Forwarded identity requires"):
        user_from_headers({}, settings.model_copy(update={"auth_mode": "unknown"}))


def test_forwarded_mode_requires_explicit_trust_and_domain() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, auth_mode="forwarded", auth_trust_forwarded_headers=False)
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            auth_mode="forwarded",
            auth_trust_forwarded_headers=True,
            auth_allowed_domains="",
        )


def test_forwarded_identity_rejects_unlisted_domain_and_mcp_unless_opted_in() -> None:
    settings = Settings(
        _env_file=None,
        auth_mode="forwarded",
        auth_trust_forwarded_headers=True,
        auth_allowed_domains="example.com",
        auth_trust_mcp_headers=False,
    )
    assert user_from_headers({"X-MCP-User-Email": "admin@example.com"}, settings) is None
    with pytest.raises(InvalidIdentityToken):
        user_from_headers({"X-Auth-Request-Email": "admin@notexample.com"}, settings)
    with pytest.raises(InvalidIdentityToken):
        user_from_headers(
            {"X-Auth-Request-Email": "operator@example.com", "Authorization": "Bearer unverified"},
            settings,
        )
    assert (
        user_from_headers({"X-Auth-Request-Email": "operator@example.com"}, settings).email
        == "operator@example.com"
    )


def test_bundle_reads_denied_without_auth_even_with_spoofed_headers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing auth configuration never turns the marketplace into public data."""
    locked = Settings(_env_file=None, auth_mode="disabled")
    monkeypatch.setattr("app.deps.get_settings", lambda: locked)
    for path in (
        "/api/bundles",
        "/api/bundles/dev/sample",
        "/api/bundles/dev/sample/versions/1/files",
        "/api/bundles/dev/sample/versions/1/archive.tgz",
        "/api/updates/check",
    ):
        if path == "/api/updates/check":
            response = client.post(
                path, json={"installed": []}, headers={"X-Auth-Request-Email": "admin@example.com"}
            )
        else:
            response = client.get(path, headers={"X-Auth-Request-Email": "admin@example.com"})
        assert response.status_code == 401, path
