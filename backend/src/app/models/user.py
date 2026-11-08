"""User model — the hub's *profile* row.

Every authenticated touch (browser SSO, `phk_` token, MCP worker header)
find-or-creates one of these; there is no signup step. See
``README.md`` → "Identity".

Two columns beyond the template's baseline:

- ``handle`` — the public, URL-safe identifier a bundle is published
  under (``/p/<handle>``, ``owner/slug`` refs). Derived from the email
  local-part on first touch (``publisher@example.com`` → ``publisher``), with a
  numeric suffix on collision. Immutable once assigned.
- ``display_name`` — user-editable, via ``PATCH /api/me``. Kept
  SEPARATE from ``name`` on purpose: ``deps`` refreshes ``name`` from
  the identity provider's claims on every request, so storing the
  editable value there would let the identity provider overwrite the user's choice on
  the next page load.
- ``is_org`` — this row is an ORG profile, not a person. Same table so an
  org can own bundles unchanged; see ``models/org.py`` for how writes on
  an org's behalf are authorized.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # `unique=True` already creates a unique index; no separate `index=True`.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    # Public identifier — the `owner` half of every `owner/slug` bundle ref.
    handle: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    # User-editable display name (PATCH /api/me). NULL means "fall back to
    # the IdP-provided `name`, then to `handle`" — see `profile_display_name`.
    display_name: Mapped[str | None] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # An ORG profile (contract → "Marketplace (v2)"). Orgs are ordinary rows
    # so they can own bundles with no special-casing in the bundle code, but
    # nobody ever *authenticates* as one: writes on an org's behalf go
    # through ``org_publishers`` (see ``models/org.py``). Never set by
    # ``find_or_create_profile`` — only by the marketplace seed.
    is_org: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # NOTE: ``deps.get_current_user`` bumps this on every authenticated
    # request, not only on a fresh sign-in — so the effective semantic is
    # "last seen / last active". The name is kept for consistency with
    # other platform apps (e.g. ``apps/machine-shop``) and to avoid an
    # alembic rename. Interpret this column as "last active" in any UI /
    # dashboard surfacing it.
    last_sign_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def profile_display_name(self) -> str:
        """Best-effort human label: the user's own choice, then the IdP
        name, then the handle. Never empty — every schema that renders a
        profile reads this instead of picking among the three columns."""
        return self.display_name or self.name or self.handle
