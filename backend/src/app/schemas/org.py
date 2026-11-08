"""Org / publisher and update-check schemas (contract → "Marketplace (v2)")."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.user import ProfileOut

# Bound on a single ``POST /api/updates/check`` batch. A phi client sends
# one entry per installed bundle; a few hundred is already an implausible
# local hub, and the cap keeps the single IN-list query bounded.
MAX_UPDATE_CHECK_ENTRIES = 500


class OrgSummaryOut(BaseModel):
    """``GET /api/orgs`` — one org, plus whether the caller may publish for it.

    ``is_publisher`` is what lets the create dialog offer "publish as
    <org>" without the client hard-coding an org handle or probing the
    roster endpoint per org.
    """

    profile: ProfileOut
    is_publisher: bool
    publisher_count: int


class PublisherOut(BaseModel):
    """One row of an org's publisher roster."""

    handle: str
    display_name: str
    email: str
    avatar_url: str | None = None
    added_at: datetime
    # Handle of whoever granted this, or ``None`` for a seeded grant / a
    # granter whose profile has since been deleted.
    added_by: str | None = None
    # True when this publisher has never signed in — i.e. the profile was
    # conjured by an email invite and is waiting for its first touch.
    pending: bool = False


class PublisherListOut(BaseModel):
    """``GET/POST/DELETE /api/orgs/{handle}/publishers``.

    Carries ``is_publisher`` for the *caller* so the UI can decide whether
    to render the add/remove controls without a second round trip and
    without re-deriving the rule client-side (contract → the add/remove
    endpoints are publishers-only).
    """

    org: ProfileOut
    publishers: list[PublisherOut]
    is_publisher: bool


class PublisherAddIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class InstalledBundleIn(BaseModel):
    """One entry of a client's installed set."""

    ref: str = Field(min_length=1, max_length=200)
    # The version currently materialized locally. 0 is legitimate: a client
    # that has a bundle linked but never resolved a version wants to be
    # told about v1.
    version: int = Field(default=0, ge=0)


class UpdateCheckIn(BaseModel):
    installed: list[InstalledBundleIn] = Field(
        default_factory=list, max_length=MAX_UPDATE_CHECK_ENTRIES
    )


class BundleUpdateOut(BaseModel):
    """One bundle with a newer published version than the client holds."""

    ref: str
    latest_version: int
    kind: str
    title: str
    changelog: str
    # Whether the publisher is an org profile. The client's default
    # ``hubAutoUpdate: "org"`` auto-applies exactly these and only notifies
    # for the rest, so the flag has to travel with the update.
    is_org: bool = False


class UpdateCheckOut(BaseModel):
    """``POST /api/updates/check`` — one round trip for a whole hub.

    ``updates`` holds only bundles strictly behind the latest published
    version; a client that is current appears in neither list. ``missing``
    holds refs that no longer resolve — soft-deleted, or never existed.
    """

    updates: list[BundleUpdateOut]
    missing: list[str]
    # Echo of how many entries were considered, so a client can spot a
    # silently truncated or mis-serialized request.
    checked: int = 0


__all__ = [
    "MAX_UPDATE_CHECK_ENTRIES",
    "BundleUpdateOut",
    "InstalledBundleIn",
    "OrgSummaryOut",
    "PublisherAddIn",
    "PublisherListOut",
    "PublisherOut",
    "UpdateCheckIn",
    "UpdateCheckOut",
]
