"""Fail-closed authentication boundaries independent of an IdP service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from pydantic import ValidationError

from app.auth_adapter import InvalidIdentityToken, user_from_headers
from app.config import Settings


def test_default_auth_is_disabled_even_with_spoofed_identity_headers() -> None:
    settings = Settings(_env_file=None, auth_mode="disabled")
    assert user_from_headers({"X-Auth-Request-Email": "admin@example.com"}, settings) is None
    assert user_from_headers({"X-MCP-User-Email": "admin@example.com"}, settings) is None


def test_dev_mode_refused_in_production() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="prod",
            environment="production",
            auth_mode="dev",
            auth_allow_insecure_dev_mode=True,
        )


def test_dev_requires_second_explicit_opt_in() -> None:
    with pytest.raises(ValidationError, match="AUTH_ALLOW_INSECURE_DEV_MODE"):
        Settings(
            _env_file=None,
            app_env="local",
            environment="development",
            auth_mode="dev",
            auth_allow_insecure_dev_mode=False,
        )


@pytest.mark.parametrize("environment", ["staging", "production", "prod", "", "preview"])
def test_dev_mode_refused_outside_explicit_allowlist(environment: str) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="local",
            environment=environment,
            auth_mode="dev",
            auth_allow_insecure_dev_mode=True,
        )
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env=environment,
            environment="local",
            auth_mode="dev",
            auth_allow_insecure_dev_mode=True,
        )


@pytest.mark.parametrize("environment", ["local", "dev", "development", "test", "DEV", "TeSt"])
def test_dev_mode_allowed_only_in_local_environments(environment: str) -> None:
    settings = Settings(
        _env_file=None,
        app_env=environment,
        environment=environment,
        auth_mode="dev",
        auth_allow_insecure_dev_mode=True,
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


@pytest.mark.parametrize("domains", ["", " ", " , ", "example.com, ", "*,example.com"])
def test_verified_auth_requires_nonempty_exact_domains(domains: str) -> None:
    with pytest.raises(ValidationError, match="AUTH_ALLOWED_DOMAINS"):
        Settings(
            _env_file=None,
            auth_mode="forwarded",
            auth_trust_forwarded_headers=True,
            auth_allowed_domains=domains,
            auth_issuer="https://login.example.org",
            auth_audience="hub",
        )


@pytest.mark.parametrize("environment", ["staging", "production", "preview", " "])
def test_loopback_mcp_hosts_rejected_outside_local_environment(environment: str) -> None:
    for environment_name, app_env in ((environment, "local"), ("local", environment)):
        with pytest.raises(ValidationError, match="MCP loopback hosts"):
            Settings(
                _env_file=None,
                environment=environment_name,
                app_env=app_env,
                auth_allow_loopback_hosts=True,
            )


@pytest.mark.parametrize("environment", ["local", "dev", "development", "test"])
def test_loopback_mcp_hosts_allowed_locally(environment: str) -> None:
    assert Settings(
        _env_file=None,
        environment=environment,
        app_env=environment,
        auth_allow_loopback_hosts=True,
    ).auth_allow_loopback_hosts


@pytest.mark.parametrize(
    "email", ["a@b@example.com", "@example.com", "a b@example.com", "a@example.com "]
)
def test_header_only_identity_rejects_malformed_email(email: str) -> None:
    settings = Settings(
        _env_file=None,
        auth_mode="forwarded",
        auth_trust_forwarded_headers=True,
        auth_allowed_domains="example.com",
        auth_issuer="https://login.example.org",
        auth_audience="hub",
    )
    with pytest.raises(InvalidIdentityToken):
        user_from_headers({"X-Auth-Request-Email": email}, settings)


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


def test_valid_signed_token_and_header_or_direct_oidc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise real RS256 signature/JWKS validation through the adapter seam."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk["kid"] = "hub-test-key"
    monkeypatch.setattr("jwt.jwks_client.PyJWKClient.fetch_data", lambda self: {"keys": [jwk]})
    settings = Settings(
        _env_file=None,
        auth_mode="forwarded",
        auth_trust_forwarded_headers=True,
        auth_allowed_domains="example.com",
        auth_issuer="https://login.example.org",
        auth_audience="hub",
        auth_jwks_url="https://login.example.org/jwks",
    )
    claims = {
        "iss": "https://login.example.org",
        "aud": "hub",
        "sub": "account-1",
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(hours=1),
        "email": "publisher@example.com",
        "email_verified": True,
        "name": "Publisher",
    }

    def token(**changes):
        return jwt.encode(
            {**claims, **changes}, key, algorithm="RS256", headers={"kid": "hub-test-key"}
        )

    headers = {
        "X-Auth-Request-Email": "publisher@example.com",
        "Authorization": f"Bearer {token()}",
    }
    assert user_from_headers(headers, settings).email == "publisher@example.com"
    assert user_from_headers(headers, settings).name == "Publisher"
    assert (
        user_from_headers(
            {"Authorization": f"bearer {token()}"},
            settings.model_copy(
                update={
                    "auth_mode": "oidc",
                    "auth_trust_forwarded_headers": False,
                    "auth_trust_mcp_headers": False,
                }
            ),
        ).email
        == "publisher@example.com"
    )
    for changes in (
        {"aud": "different"},
        {"iss": "https://evil.example.org"},
        {"email_verified": False},
        {"email": "publisher@other.example"},
    ):
        with pytest.raises(InvalidIdentityToken):
            user_from_headers({**headers, "Authorization": f"Bearer {token(**changes)}"}, settings)
    with pytest.raises(InvalidIdentityToken):
        user_from_headers({**headers, "X-Auth-Request-Email": "admin@example.com"}, settings)
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    invalid_signature = jwt.encode(
        claims, forged, algorithm="RS256", headers={"kid": "hub-test-key"}
    )
    with pytest.raises(InvalidIdentityToken):
        user_from_headers({**headers, "Authorization": f"Bearer {invalid_signature}"}, settings)


def test_mcp_header_opt_in_and_bad_bearer_never_downgrades() -> None:
    settings = Settings(
        _env_file=None,
        auth_mode="forwarded",
        auth_trust_forwarded_headers=True,
        auth_trust_mcp_headers=False,
        auth_allowed_domains="example.com",
        auth_issuer="https://login.example.org",
        auth_audience="hub",
    )
    mcp_headers = {"X-MCP-User-Email": "publisher@example.com"}
    assert user_from_headers(mcp_headers, settings) is None
    enabled = settings.model_copy(update={"auth_trust_mcp_headers": True})
    assert user_from_headers(mcp_headers, enabled) is None
    assert (
        user_from_headers(mcp_headers, enabled, allow_mcp_headers=True).email
        == "publisher@example.com"
    )
    for bearer in ("Bearer invalid", "bearer invalid", "Bearer ", "Basic invalid"):
        with pytest.raises(InvalidIdentityToken):
            user_from_headers(
                {**mcp_headers, "Authorization": bearer}, enabled, allow_mcp_headers=True
            )


def test_rest_401_does_not_expose_verifier_reason(client: TestClient) -> None:
    response = client.get("/api/me", headers={"X-Auth-Request-Email": "intruder@unlisted.example"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"
    assert response.headers["WWW-Authenticate"] == "Bearer"


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
