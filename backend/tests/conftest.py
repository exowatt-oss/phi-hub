"""Shared pytest fixtures.

Tests run against the local Postgres from
``docker compose -f docker-compose.dev.yml --profile db up``. Each test gets
a clean schema via TRUNCATE.

The tests opt into forwarded-header mode with synthetic example.com users.
This mode is safe only here because TestClient supplies controlled headers;
a real deployment must use an authenticated proxy and isolate the backend.
The default installation stays disabled and cannot read bundles anonymously.

**Divergence from the template**: the ``client`` fixture builds a FRESH
``create_app()`` per test instead of reusing the module-level ``app``.
phi hub mounts an MCP sub-app, and ``StreamableHTTPSessionManager.run()``
may only be called once per instance — entering the lifespan of the same
app twice raises "run() can only be called once per instance". A fresh
app per test is exactly what ``build_mcp_app()``'s factory shape exists for.
"""

from __future__ import annotations

import os

# Use synthetic, explicit forwarded-header mode for route-level tests.
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("AUTH_MODE", "forwarded")
os.environ.setdefault("AUTH_TRUST_FORWARDED_HEADERS", "true")
os.environ.setdefault("AUTH_ALLOWED_DOMAINS", "example.com")
os.environ.setdefault("AUTH_EMAIL_HEADER", "X-Auth-Request-Email")
os.environ.setdefault("AUTH_TRUST_MCP_HEADERS", "true")
os.environ.setdefault("AUTH_ALLOWED_HOSTS", "testserver")
# Marketplace seed (contract → "Marketplace (v2)"). Pinned here rather than
# left to the production default so the suite's expectations about who the
# initial publisher is don't move when the deployed roster does. Every app
# built by the `client` fixture re-runs the seed, which is also how the
# idempotence tests get their second run for free.
os.environ.setdefault("ORG_HANDLE", "example")
os.environ.setdefault("ORG_DISPLAY_NAME", "Example")
os.environ.setdefault("ORG_EMAIL", "hub-org@example.com")
os.environ.setdefault("ORG_INITIAL_PUBLISHERS", "publisher@example.com")

import base64  # noqa: E402
from collections.abc import Generator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402
from app.main import create_app  # noqa: E402

# Every table the suite writes to. A TRUNCATE of `users` alone would reach
# the rest via CASCADE, but naming them keeps the reset explicit — a new
# table nobody added here fails loudly instead of leaking rows between tests.
_TABLES = (
    "import_events",
    "bundle_files",
    "bundle_versions",
    "bundles",
    "org_publishers",
    "device_auths",
    "api_tokens",
    "users",
)


def b64(content: str) -> str:
    """Encode a string the way a publish payload carries it."""
    return base64.b64encode(content.encode("utf-8")).decode("ascii")


def _running_in_ci() -> bool:
    return os.environ.get("CI", "").lower() in {"1", "true", "yes"}


@pytest.fixture(autouse=True)
def _clean_db() -> Generator[None, None, None]:
    try:
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
    except Exception as exc:  # noqa: BLE001
        # Skipping is fine on a developer laptop — but in CI it would mask
        # regressions, so fail loudly there. Provision an ephemeral Postgres
        # in your CI workflow before running pytest.
        if _running_in_ci():
            pytest.fail(
                f"Postgres not reachable in CI: {exc}. Provision a test DB before running pytest."
            )
        pytest.skip("Postgres not reachable — start it with `task dev:infra`")
    yield


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    """Anonymous client against a fresh app instance (see module docstring)."""
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def auth_client(client: TestClient) -> TestClient:
    """A synthetic user authenticated by the configured test-only header mode."""
    client.headers.update(
        {
            "X-Auth-Request-Email": "dev@example.com",
            "X-Auth-Request-User": "dev",
        }
    )
    return client


@pytest.fixture()
def other_client() -> Generator[TestClient, None, None]:
    """A SECOND signed-in user, for ownership and install-count tests.

    It gets its own app instance because two ``TestClient``s can't share
    one (the MCP session manager is single-run per app).
    """
    with TestClient(create_app()) as c:
        c.headers.update(
            {
                "X-Auth-Request-Email": "other@example.com",
                "X-Auth-Request-User": "other",
            }
        )
        yield c


@pytest.fixture()
def publisher_client() -> Generator[TestClient, None, None]:
    """Synthetic publisher of the test-only ``example`` org."""
    with TestClient(create_app()) as c:
        c.headers.update(
            {
                "X-Auth-Request-Email": "publisher@example.com",
                "X-Auth-Request-User": "publisher",
            }
        )
        yield c


@pytest.fixture()
def published_bundle(auth_client: TestClient) -> dict:
    """A bundle owned by ``dev@example.com`` with one published version."""
    created = auth_client.post(
        "/api/bundles",
        json={
            "slug": "routing-setup",
            "title": "Routing setup",
            "description": "Model routing that actually works.",
            "kind": "settings",
            "tags": ["routing"],
        },
    )
    assert created.status_code == 201, created.text
    published = auth_client.post(
        "/api/bundles/dev/routing-setup/versions",
        json={
            "changelog": "first cut",
            "files": [
                {"path": "skills/routing/SKILL.md", "content_b64": b64("# routing\n")},
                {"path": "settings/settings.json", "content_b64": b64('{"model": "opus"}')},
            ],
        },
    )
    assert published.status_code == 201, published.text
    return auth_client.get("/api/bundles/dev/routing-setup").json()
