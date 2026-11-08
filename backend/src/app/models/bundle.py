"""Bundles — the registry's unit of sharing.

A bundle is a named, owned container; its *content* lives in immutable
``BundleVersion`` rows, each holding a set of ``BundleFile`` blobs.
Publishing never mutates a previous version — ``POST .../versions``
always creates N+1. That makes `owner/slug@v3` a stable reference and
lets the phi client materialize a version into an isolated store
directory without ever re-reading the server.

``ImportEvent`` is the telemetry stream behind the install count. The
count is deliberately NOT a denormalized column: the contract defines it
as "distinct profiles whose *latest* event is imported/enabled", so a
disable must be able to decrement it. See ``app.hub.install_count_subquery``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db import Base
from app.models.user import User

# `kind` labels a bundle's primary subtype (contract → "Bundle format").
# `models` bundles ship a single canonical `models/models.json` provider
# catalog — see `app.hub.validate_models_json` for the safety rules that let
# that one basename through the secret guard.
BUNDLE_KINDS = (
    "skill",
    "extension",
    "prompt",
    "theme",
    "instructions",
    "settings",
    "keybindings",
    "models",
    "mixed",
)

# Import-telemetry verbs (contract → REST table, POST .../events).
IMPORT_ACTIONS = ("imported", "enabled", "disabled", "removed", "applied")

# The subset of actions that count as "this profile currently has it".
INSTALLED_ACTIONS = ("imported", "enabled")


class Bundle(Base):
    __tablename__ = "bundles"
    __table_args__ = (
        UniqueConstraint("owner_id", "slug", name="uq_bundles_owner_slug"),
        # Browse filters by kind and orders by recency; both are the whole
        # of the default query plan on the home page.
        Index("ix_bundles_kind", "kind"),
        Index("ix_bundles_updated_at", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    owner_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="mixed")
    # Free-form label list. JSON rather than PG ARRAY so the column stays
    # dialect-neutral; the hub never queries *into* it (search matches
    # title/description/slug), it only round-trips it to the UI.
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Denormalized pointer to the highest published version number. 0 means
    # "created but never published" — the UI shows those as drafts.
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    # Soft delete — the contract's DELETE is "owner soft-delete", so refs in
    # other people's `hub/state.json` degrade to "gone" rather than 404-ing
    # a row that took their import telemetry with it.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner: Mapped[User] = relationship(User, lazy="joined")


class BundleVersion(Base):
    __tablename__ = "bundle_versions"
    __table_args__ = (
        UniqueConstraint("bundle_id", "version", name="uq_bundle_versions_bundle_version"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    bundle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("bundles.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    changelog: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # The `pi` block a pi package may carry in its package.json. Stored as
    # given so the client can write it back out verbatim on materialize.
    pi_manifest: Mapped[dict | None] = mapped_column(JSON)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    bundle: Mapped[Bundle] = relationship(Bundle, lazy="joined")


class BundleFile(Base):
    __tablename__ = "bundle_files"
    __table_args__ = (UniqueConstraint("version_id", "path", name="uq_bundle_files_version_path"),)

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("bundle_versions.id", ondelete="CASCADE"), nullable=False
    )
    # Bundle-relative POSIX path, e.g. `skills/routing/SKILL.md`. Validated
    # on publish (no `..`, no leading `/`, no `.git/`).
    path: Mapped[str] = mapped_column(String(400), nullable=False)
    # Decoded bytes. Bundles are config text measured in KB — a DB column
    # avoids an object-store dependency and keeps archive.tgz a pure read.
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    # Whether `content` decodes as UTF-8. Drives the detail page's
    # "show it" vs "binary, download only" branch without re-decoding.
    is_text: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class ImportEvent(Base):
    __tablename__ = "import_events"
    # Exactly the access path of the install-count window function
    # (partition by bundle+user, order by time).
    __table_args__ = (
        Index("ix_import_events_bundle_user_time", "bundle_id", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    bundle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("bundles.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(User, lazy="joined")
