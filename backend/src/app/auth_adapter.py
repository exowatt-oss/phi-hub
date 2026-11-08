"""Temporary identity adapter until the public ``forwarded-auth`` release.

This is the ONLY integration seam to replace when forwarded-auth becomes
available. The default is disabled, not anonymous: even bundle reads require
an identity. ``forwarded`` trusts headers ONLY when a correctly configured
reverse proxy authenticates users, strips caller-supplied identity headers,
and blocks direct access to this backend. It does not validate a bearer JWT.
Never expose forwarded mode on a directly reachable backend.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.config import Settings


@dataclass(frozen=True)
class Identity:
    email: str
    name: str | None = None
    picture: str | None = None


class InvalidIdentityToken(ValueError):
    """An identity was presented but did not pass configured policy."""


def user_from_headers(headers: Mapping[str, str], settings: Settings) -> Identity | None:
    """Return verified-by-edge identity, local synthetic identity, or none.

    No incoming header is trusted in disabled mode. Header names are
    configurable; allowed domains are exact, not suffix matches. An incoming
    ``phk_`` bearer is resolved independently in deps.py before this call.
    """
    if settings.auth_mode == "disabled":
        return None
    if settings.auth_mode == "dev":
        return Identity(settings.dev_bypass_email.lower(), settings.dev_bypass_name)

    if settings.auth_mode != "forwarded" or not settings.auth_trust_forwarded_headers:
        raise ValueError("Forwarded identity requires AUTH_MODE=forwarded and trusted headers")
    raw = {key.lower(): value for key, value in headers.items()}
    # Do not downgrade a presented but unverified bearer into a trusted
    # header-only identity. The public forwarded-auth replacement will verify
    # the issuer and audience; this temporary adapter deliberately refuses it.
    if raw.get("authorization", "").lower().startswith("bearer "):
        raise InvalidIdentityToken("Bearer identity verification is not configured")
    email = raw.get(settings.auth_email_header.lower(), "").strip().lower()
    name_header = settings.auth_name_header
    if not email and settings.auth_trust_mcp_headers:
        email = raw.get(settings.auth_mcp_email_header.lower(), "").strip().lower()
        name_header = settings.auth_mcp_name_header
    if not email:
        return None
    local, separator, domain = email.partition("@")
    allowed = {
        part.strip().lower() for part in settings.auth_allowed_domains.split(",") if part.strip()
    }
    if (
        not separator
        or not local
        or not domain
        or "@" in domain
        or any(char.isspace() for char in local)
        or domain not in allowed
    ):
        raise InvalidIdentityToken("Forwarded email is invalid or outside allowed domains")
    return Identity(email=email, name=raw.get(name_header.lower()) or None)


def build_transport_security(settings: Settings):
    """Keep FastMCP's DNS-rebinding checks enabled for local and configured hosts."""
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", "testserver"]
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    for host in (part.strip() for part in settings.auth_allowed_hosts.split(",")):
        if host:
            hosts.extend([host, f"{host}:*"])
            origins.append(f"https://{host}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )
