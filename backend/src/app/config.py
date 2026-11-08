"""Pydantic-driven settings for the backend.

Defaults fail closed for authentication. Configure a trusted identity provider
before serving real bundle data; `.env.example` shows local development.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All env-driven configuration. See `.env.example` for the contract."""

    model_config = SettingsConfigDict(
        env_file=(Path(__file__).resolve().parents[3] / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -------- Core --------
    app_env: str = Field(default="prod")
    environment: str = Field(default="production")
    app_base_url: str = Field(default="")
    log_level: str = Field(default="INFO")

    # -------- Database --------
    database_url: str = Field(default="postgresql+psycopg://dev:dev@127.0.0.1:55433/app")

    # -------- Auth --------
    # No production identity is trusted by default. Forwarded mode requires
    # an authenticated reverse proxy that strips incoming identity headers
    # and prevents direct access to the app port. This temporary adapter is
    # replaced by forwarded-auth when its public package is available.
    auth_mode: str = Field(default="disabled")  # disabled | dev | forwarded
    auth_trust_forwarded_headers: bool = Field(default=False)
    auth_email_header: str = Field(default="X-Auth-Request-Email")
    auth_name_header: str = Field(default="X-Auth-Request-Name")
    auth_trust_mcp_headers: bool = Field(default=False)
    auth_mcp_email_header: str = Field(default="X-MCP-User-Email")
    auth_mcp_name_header: str = Field(default="X-MCP-User-Name")
    auth_allowed_domains: str = Field(default="")  # comma-separated, exact domains
    auth_logout_url: str = Field(default="")
    auth_allowed_hosts: str = Field(default="")  # comma-separated MCP Host allowlist
    dev_bypass_email: str = Field(default="dev@example.org")
    dev_bypass_name: str = Field(default="Dev User")

    # -------- Hub --------
    # The URL the hub tells other software to talk to. It ends up in the
    # device flow's ``verify_url`` (the link a user opens to approve a CLI),
    # in the import snippets the UI and MCP tools hand out, and in the phi
    # client's default ``hub.json``. ``APP_BASE_URL`` wins when set, so a
    # local dev run points at the Vite origin instead of production.
    hub_public_url: str = Field(default="http://localhost:5173")

    # Device-authorization tuning (contract → "Device flow"). The expiry is
    # the security-relevant one: an unapproved request dies in 15 minutes,
    # which also bounds how long a minted-but-uncollected token sits at rest.
    device_code_ttl_seconds: int = Field(default=15 * 60)
    device_poll_interval_seconds: int = Field(default=3)

    # -------- Marketplace (contract → "Marketplace (v2)") --------
    # The org profile the marketplace curates. Seeded idempotently at app
    # startup by ``app.orgs.ensure_seed`` (NOT by a migration — see
    # ``alembic/versions/0003_marketplace.py`` for why).
    # Opt-in: no org or initial publisher is created by a fresh installation.
    org_handle: str = Field(default="")
    org_display_name: str = Field(default="")
    org_email: str = Field(default="")
    # Comma-separated emails granted publish rights on first boot. Profiles
    # that do not exist yet are created as stubs (same handle derivation as
    # a real sign-in). Adding an email here and redeploying grants it;
    # REMOVING one does not revoke — revocation is an explicit DELETE
    # through the API, so a config edit can never silently strip access.
    org_initial_publishers: str = Field(default="")

    @property
    def initial_publisher_emails(self) -> list[str]:
        """``ORG_INITIAL_PUBLISHERS`` parsed: lowercased, de-duplicated,
        blanks dropped, original order preserved."""
        out: list[str] = []
        for raw in (self.org_initial_publishers or "").split(","):
            email = raw.strip().lower()
            if email and email not in out:
                out.append(email)
        return out

    # -------- CORS --------
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @property
    def public_base_url(self) -> str:
        """Canonical, trailing-slash-free base URL for outward-facing links."""
        return (self.app_base_url or self.hub_public_url).rstrip("/")

    @model_validator(mode="after")
    def normalize_database_driver(self) -> Settings:
        """Force the SQLAlchemy ``postgresql+psycopg://`` dialect prefix.

        SQLAlchemy needs ``postgresql+psycopg://`` to select psycopg 3.
        Plain ``postgresql://`` and ``postgres://`` URLs are normalized.
        Raw libpq clients should instead use a driver-free URL."""
        url = self.database_url
        if url.startswith("postgresql://"):
            self.database_url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        elif url.startswith("postgres://"):
            self.database_url = url.replace("postgres://", "postgresql+psycopg://", 1)
        return self

    @model_validator(mode="after")
    def validate_auth_mode(self) -> Settings:
        """Refuse dev auth in production, and reject incomplete forwarded auth."""
        if self.auth_mode not in {"disabled", "dev", "forwarded"}:
            raise ValueError("AUTH_MODE must be disabled, dev, or forwarded")
        local_environments = {"local", "dev", "development", "test"}
        if self.auth_mode == "dev" and any(
            value.strip().lower() not in local_environments
            for value in (self.environment, self.app_env)
        ):
            raise ValueError("AUTH_MODE=dev requires a local or test ENVIRONMENT and APP_ENV")
        if self.auth_mode == "forwarded" and (
            not self.auth_trust_forwarded_headers or not self.auth_allowed_domains.strip()
        ):
            raise ValueError("forwarded auth needs a trusted proxy and AUTH_ALLOWED_DOMAINS")
        return self

    @property
    def is_prod(self) -> bool:
        return (
            self.environment.strip().lower() == "production"
            or self.app_env.strip().lower() == "prod"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
