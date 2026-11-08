"""Org profiles and their publishers — the marketplace's trust boundary.

An **org profile** is an ordinary ``users`` row with ``is_org = true``. It
owns bundles exactly the way a person does (``owner_id`` on ``bundles``),
which is what makes ``example/routing-setup`` a valid ``owner/slug``
reference with no special-casing anywhere in the bundle code.

What an org does NOT have is a way to sign in. Nobody authenticates *as*
``example``; instead ``org_publishers`` names the people allowed to write
on its behalf. Every owner-scoped write therefore asks two questions
("are you the owner?" then "are you a publisher of the owning org?")
rather than one — see ``app.orgs.can_write``.

``bundle_versions.created_by_id`` still records the actual human who
published, so "who shipped v4 of the org bundle" survives a publisher
being removed later.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class OrgPublisher(Base):
    """One person's permission to publish under one org profile.

    Both ``org_id`` and ``user_id`` point at ``users`` — the org is a user
    row too. The pair is unique so "add a publisher twice" is a no-op
    rather than a duplicate grant, which is what lets the startup seed be
    idempotent with a plain INSERT … ON CONFLICT DO NOTHING.

    ``added_by_id`` is SET NULL rather than CASCADE: losing the person who
    granted a permission must not silently revoke the permission.
    """

    __tablename__ = "org_publishers"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_org_publishers_org_user"),
        # Reverse lookup: "which orgs may this caller write for?" — asked on
        # every owner-scoped bundle write, so it gets its own index. The
        # forward direction (an org's publisher list) rides the unique
        # constraint's index.
        Index("ix_org_publishers_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    org_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    added_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
