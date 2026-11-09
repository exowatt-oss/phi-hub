"""Single REST/MCP identity seam backed by the public forwarded-auth package.

No identity is trusted by default. Browser/proxy headers are trusted without a
JWT only after an explicit forwarded-mode opt-in, an exact domain allowlist,
and an authenticated header-stripping edge. phk_ app tokens are handled first
by deps.py; all other bearer credentials must be verified here or rejected.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from forwarded_auth import InvalidIdentityToken
from forwarded_auth import Settings as ForwardedSettings
from forwarded_auth import user_from_headers as resolve_identity
from forwarded_auth.mcp import build_transport_security as forwarded_transport_security

from app.config import Settings


@dataclass(frozen=True)
class Identity:
    """Hub profile fields; keep missing display names blank for handle fallback."""

    email: str
    name: str | None = None
    picture: str | None = None


def _validate_email_shape(email: str) -> None:
    if email.count("@") != 1 or not email.split("@", 1)[0] or any(c.isspace() for c in email):
        raise InvalidIdentityToken("Invalid email address")


def _as_identity(user) -> Identity | None:
    if user is None:
        return None
    _validate_email_shape(user.email)
    return Identity(
        user.email.lower(), user.name if user.name != user.email else None, user.picture
    )


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _config(settings: Settings) -> ForwardedSettings:
    """Build per-request immutable config; never depend on package-global env state."""
    if settings.auth_mode == "dev":
        if not settings.auth_allow_insecure_dev_mode:
            raise ValueError("Dev identity requires AUTH_ALLOW_INSECURE_DEV_MODE=true")
        if settings.is_prod or any(
            value.strip().lower() not in {"local", "dev", "development", "test"}
            for value in (settings.environment, settings.app_env)
        ):
            raise ValueError("Dev identity is not allowed in production")
        return ForwardedSettings(
            mode="none",
            allow_insecure_none_mode=settings.auth_allow_insecure_dev_mode,
            dev_user_email=settings.dev_bypass_email.lower(),
            dev_user_name=settings.dev_bypass_name,
            dev_user_id=settings.dev_bypass_email.lower(),
            trusted_hostnames=_csv(settings.auth_allowed_hosts),
            allow_loopback_hosts=True,
        )

    # Disabled mode never invokes the library's default (forwarded) identity.
    # Its MCP transport remains fail-closed until explicit hosts are supplied.
    if settings.auth_mode == "disabled":
        return ForwardedSettings(
            mode="forwarded", trusted_hostnames=_csv(settings.auth_allowed_hosts)
        )
    if settings.auth_mode not in {"forwarded", "oidc"}:
        raise ValueError("Forwarded identity requires a supported AUTH_MODE")
    if settings.auth_mode == "forwarded" and not settings.auth_trust_forwarded_headers:
        raise ValueError("Forwarded identity requires AUTH_TRUST_FORWARDED_HEADERS=true")
    if not settings.auth_issuer or not settings.auth_audience or not settings.auth_allowed_domains:
        raise ValueError(
            "Verified identity needs AUTH_ISSUER, AUTH_AUDIENCE and AUTH_ALLOWED_DOMAINS"
        )

    config = ForwardedSettings(
        mode=settings.auth_mode,
        issuer=settings.auth_issuer,
        jwks_url=settings.auth_jwks_url or None,
        audience=_csv(settings.auth_audience),
        allowed_email_domains=frozenset(
            part.lower() for part in _csv(settings.auth_allowed_domains)
        ),
        email_header=settings.auth_email_header,
        preferred_username_header=settings.auth_name_header,
        # This is the *explicit* header-only, authenticated second-hop mode.
        # Never turn off JWT verification; a presented bearer must be valid.
        trust_proxy_headers=settings.auth_mode == "forwarded"
        and settings.auth_trust_forwarded_headers,
        proxy_email_header=settings.auth_email_header,
        proxy_name_header=settings.auth_name_header,
        trusted_hostnames=_csv(settings.auth_allowed_hosts),
        allow_loopback_hosts=settings.auth_allow_loopback_hosts,
    )
    config.validate()
    return config


def user_from_headers(
    headers: Mapping[str, str], settings: Settings, *, allow_mcp_headers: bool = False
):
    """Resolve identity; MCP's separate headers are only for the /mcp caller."""
    if settings.auth_mode == "disabled":
        return None
    config = _config(settings)
    if settings.auth_mode == "dev":
        return _as_identity(resolve_identity(headers, settings=config))

    # The library only recognizes capitalized `Bearer `; reject ambiguous or
    # malformed Authorization rather than ever falling through to proxy trust.
    raw = {key.lower(): value for key, value in headers.items()}
    authorization = raw.get("authorization", "")
    if authorization:
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() != "bearer" or not credential.strip():
            raise InvalidIdentityToken("Invalid Authorization header")
        raw["authorization"] = f"Bearer {credential.strip()}"

    if allow_mcp_headers and settings.auth_mode == "forwarded" and settings.auth_trust_mcp_headers:
        primary = raw.get(settings.auth_email_header.lower(), "")
        if not primary and raw.get(settings.auth_mcp_email_header.lower(), ""):
            # Reuse the package's same exact-domain policy for the *separate*
            # MCP edge; never let the alternate headers bypass JWT checks.
            config = replace(
                config,
                email_header=settings.auth_mcp_email_header,
                preferred_username_header=settings.auth_mcp_name_header,
                proxy_email_header=settings.auth_mcp_email_header,
                proxy_name_header=settings.auth_mcp_name_header,
            )
    # The package normalizes header whitespace before returning a user, so
    # inspect the original header to reject malformed header-only identities.
    header_email = raw.get(config.proxy_email_header.lower())
    if header_email is not None:
        _validate_email_shape(header_email)
    return _as_identity(resolve_identity(raw, settings=config))


def build_transport_security(settings: Settings):
    """Preserve FastMCP DNS-rebinding protection; no prod loopback by default."""
    return forwarded_transport_security(settings=_config(settings))
