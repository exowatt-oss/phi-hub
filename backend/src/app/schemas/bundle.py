"""Bundle-shaped request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.bundle import BUNDLE_KINDS, IMPORT_ACTIONS
from app.schemas.user import ProfileOut

BundleKind = Literal[
    "skill",
    "extension",
    "prompt",
    "theme",
    "instructions",
    "settings",
    "keybindings",
    "models",
    "mixed",
]
ImportAction = Literal["imported", "enabled", "disabled", "removed", "applied"]


class BundleSummaryOut(BaseModel):
    """One card on the browse page / one row in a profile's bundle list."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ref: str
    owner: str
    owner_display_name: str
    slug: str
    title: str
    description: str
    kind: str
    tags: list[str]
    latest_version: int
    install_count: int = 0
    created_at: datetime
    updated_at: datetime


class BundleFileOut(BaseModel):
    """A file listing entry — path + size, no bytes."""

    path: str
    size: int
    is_text: bool


class BundleFileContentOut(BaseModel):
    """A file WITH its bytes, base64-encoded. What the phi client
    materializes an import from (no tar dependency on the client side)."""

    path: str
    size: int
    is_text: bool
    content_b64: str


class BundleVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: int
    changelog: str
    file_count: int
    total_bytes: int
    created_at: datetime
    created_by: str | None = None
    # The `pi` block from the bundle's package.json, stored verbatim so a
    # materializing client can write it back out unchanged.
    pi_manifest: dict[str, Any] | None = None


class ImportEventOut(BaseModel):
    action: str
    version: int | None = None
    handle: str
    display_name: str
    created_at: datetime


class ImportSnippetsOut(BaseModel):
    """The copyable snippets the Import modal shows.

    ``phi_install`` (track latest) is the primary one; ``phi_pin`` freezes
    the bundle at the version shown. ``phi_command`` is the bare
    ``/hub import`` form — same behaviour as ``phi_install``, kept because
    every existing client and doc already says it.
    """

    phi_install: str = ""
    phi_command: str
    phi_pin: str = ""
    agent_prompt: str
    curl: str


class BundleDetailOut(BaseModel):
    """``GET /api/bundles/{owner}/{slug}`` — everything the detail page and
    ``hub_get_bundle`` need in one round trip.

    The bundle's own metadata lives under ``bundle``; ``ref`` and
    ``latest_version`` are ALSO mirrored to the top level. A client
    materializing an import needs the version number to build the
    ``v<N>`` store path, and reaching for ``detail.latest_version`` is the
    obvious guess — returning ``undefined`` there would send it to
    ``…/vundefined/``. The mirrors are set by a validator so they cannot
    drift from ``bundle``.
    """

    bundle: BundleSummaryOut
    ref: str = ""
    latest_version: int = 0
    owner_profile: ProfileOut
    versions: list[BundleVersionOut]
    files: list[BundleFileOut]
    recent_events: list[ImportEventOut]
    import_snippets: ImportSnippetsOut
    import_instructions: dict[str, Any]
    is_owner: bool
    # "May the viewer edit / delete / publish here." Equals ``is_owner`` for
    # a personal bundle; for an ORG-owned one it is also true for the org's
    # publishers. Separate field so ``is_owner`` keeps meaning exactly what
    # it always meant.
    can_write: bool = False

    @model_validator(mode="after")
    def _mirror_bundle_fields(self) -> BundleDetailOut:
        self.ref = self.bundle.ref
        self.latest_version = self.bundle.latest_version
        return self


class BundleCreateIn(BaseModel):
    slug: str = Field(min_length=2, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    kind: BundleKind = "mixed"
    tags: list[str] = Field(default_factory=list, max_length=20)
    # Publish under an ORG profile instead of yourself (contract →
    # "Publishing to an org"). Omit — or pass your own handle — to publish
    # personally. 404 if there is no such org, 403 if you are not one of
    # its publishers.
    owner: str | None = Field(default=None, max_length=64)

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        """Lowercase, de-duplicate, drop blanks, cap tag length. Tags are a
        browsing aid, not a namespace — keeping them tidy server-side means
        the UI never has to render `Routing` and `routing` as two chips."""
        out: list[str] = []
        for tag in v:
            cleaned = (tag or "").strip().lower()[:40]
            if cleaned and cleaned not in out:
                out.append(cleaned)
        return out


class BundleUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    kind: BundleKind | None = None
    tags: list[str] | None = Field(default=None, max_length=20)

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return BundleCreateIn._clean_tags(v)


class PublishFileIn(BaseModel):
    path: str = Field(min_length=1, max_length=400)
    content_b64: str = Field(default="")


class VersionPublishIn(BaseModel):
    changelog: str = Field(default="", max_length=4000)
    pi_manifest: dict[str, Any] | None = None
    files: list[PublishFileIn]


class VersionDetailOut(BaseModel):
    """``GET .../versions/{v}`` — a version's metadata + file listing."""

    ref: str
    version: BundleVersionOut
    files: list[BundleFileOut]


class VersionFilesOut(BaseModel):
    """``GET .../versions/{v}/files`` — full contents.

    Carries ``pi_manifest`` alongside the files because this is the endpoint
    a client materializes an import from: without it the client could write
    every file but not reconstruct the package's own manifest.
    """

    ref: str
    version: int
    pi_manifest: dict[str, Any] | None = None
    files: list[BundleFileContentOut]


class ImportEventIn(BaseModel):
    action: ImportAction
    version: int | None = Field(default=None, ge=1)


class BundleListOut(BaseModel):
    """``GET /api/bundles`` — the browse/search envelope.

    ``bundles`` and ``items`` are the SAME list under two names. The contract
    names the endpoint but not its envelope key, and the two clients built
    against it read different ones (the web UI reads ``bundles``; a client
    written to the common ``items`` convention reads ``items``). Serving both
    costs one duplicated list of tens of rows and removes an entire class of
    silent integration failure — a client reading the wrong key gets
    ``undefined`` and renders an empty registry with no error anywhere.

    ``items`` is populated by a validator, so callers construct this with
    ``bundles=`` alone and the alias can never drift out of sync.
    """

    bundles: list[BundleSummaryOut]
    items: list[BundleSummaryOut] = Field(default_factory=list)
    total: int

    @model_validator(mode="after")
    def _mirror_items(self) -> BundleListOut:
        self.items = self.bundles
        return self


class ProfileDetailOut(BaseModel):
    """``GET /api/profiles/{handle}`` — a profile plus what they publish.

    Lives here rather than in ``schemas/user`` so the forward reference to
    ``BundleSummaryOut`` resolves without a ``model_rebuild()`` dance."""

    profile: ProfileOut
    bundles: list[BundleSummaryOut]


# Re-exported so routers can validate against the model layer's tuples
# without importing from two places.
__all__ = [
    "BUNDLE_KINDS",
    "IMPORT_ACTIONS",
    "BundleCreateIn",
    "BundleDetailOut",
    "BundleFileContentOut",
    "BundleFileOut",
    "BundleKind",
    "BundleListOut",
    "BundleSummaryOut",
    "BundleUpdateIn",
    "BundleVersionOut",
    "ImportAction",
    "ImportEventIn",
    "ImportEventOut",
    "ImportSnippetsOut",
    "ProfileDetailOut",
    "PublishFileIn",
    "VersionDetailOut",
    "VersionFilesOut",
    "VersionPublishIn",
]
