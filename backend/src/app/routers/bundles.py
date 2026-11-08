"""Bundles: browse, create, publish versions, fetch files, record imports.

Reads are org-wide. Writes are **owner-or-publisher**: the caller must own
the bundle, or be a publisher of the ORG profile that owns it (contract →
"Marketplace (v2)"). That check runs against the *bundle's* ``owner_id``
rather than the URL's ``{owner}`` segment, so a request to
``PATCH /api/bundles/someone-else/thing`` fails authorization rather than
quietly editing a bundle the path implied.

``app.orgs.require_write`` owns the rule; nothing in this module
re-implements it.
"""

from __future__ import annotations

import base64

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import hub, orgs, queries
from app.db import get_db
from app.deps import get_current_user
from app.models.bundle import Bundle, BundleFile, BundleVersion
from app.models.user import User
from app.schemas.bundle import (
    BundleCreateIn,
    BundleDetailOut,
    BundleFileContentOut,
    BundleListOut,
    BundleSummaryOut,
    BundleUpdateIn,
    ImportEventIn,
    ImportEventOut,
    VersionDetailOut,
    VersionFilesOut,
    VersionPublishIn,
)

router = APIRouter(prefix="/bundles", tags=["bundles"])
log = structlog.get_logger("bundles")

# Browse-page page size. The hub is a private registry measured in tens
# of bundles, so this is a sanity bound, not real pagination pressure.
_MAX_LIMIT = 200


def _publish_error(exc: hub.PublishRejected) -> HTTPException:
    """PublishRejected → 422 naming the offending path (contract)."""
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.as_detail())


@router.get("", response_model=BundleListOut)
def list_bundles(
    q: str | None = Query(default=None, max_length=200),
    kind: str | None = Query(default=None, max_length=20),
    owner: str | None = Query(default=None, max_length=64),
    org_only: bool = Query(
        default=False,
        description="Only bundles owned by an org profile — the curated marketplace view.",
    ),
    sort: str = Query(default=queries.SORT_UPDATED, pattern="^(updated|popular)$"),
    limit: int = Query(default=100, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BundleListOut:
    """Search / browse. Org-wide; soft-deleted bundles are excluded."""
    bundles, total = queries.search_bundles(
        db,
        q=q,
        kind=kind,
        owner=owner,
        org_only=org_only,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return BundleListOut(bundles=bundles, total=total)


@router.post("", response_model=BundleSummaryOut, status_code=status.HTTP_201_CREATED)
def create_bundle(
    payload: BundleCreateIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BundleSummaryOut:
    """Create an empty bundle. Publish content into it with POST .../versions.

    ``owner`` publishes under an ORG profile instead of the caller — 404 if
    there is no such org, 403 if the caller is not one of its publishers.
    The bundle belongs to the org from then on; the caller's identity is
    still recorded on every version they publish into it.
    """
    try:
        hub.validate_slug(payload.slug)
    except hub.PublishRejected as exc:
        raise _publish_error(exc) from exc

    owner_user = orgs.resolve_publish_owner(db, current, payload.owner)

    bundle = Bundle(
        owner_id=owner_user.id,
        slug=payload.slug,
        title=payload.title.strip(),
        description=payload.description.strip(),
        kind=payload.kind,
        tags=payload.tags,
    )
    db.add(bundle)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"You already publish a bundle called '{payload.slug}'."
                if owner_user.id == current.id
                else f"'{owner_user.handle}' already publishes a bundle called '{payload.slug}'."
            ),
        ) from exc
    db.refresh(bundle)
    log.info("bundle_created", ref=f"{owner_user.handle}/{bundle.slug}", by=current.handle)
    return queries.bundle_summary(db, bundle, owner_user)


@router.get("/{owner}/{slug}", response_model=BundleDetailOut)
def get_bundle(
    owner: str,
    slug: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BundleDetailOut:
    """Metadata, versions, latest file listing, install count, import snippets."""
    bundle, owner_user = queries.get_bundle_or_404(db, owner, slug)
    return queries.bundle_detail(db, bundle, owner_user, viewer=current)


@router.patch("/{owner}/{slug}", response_model=BundleDetailOut)
def update_bundle(
    owner: str,
    slug: str,
    payload: BundleUpdateIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BundleDetailOut:
    """Owner (or org publisher) metadata edit. The slug is immutable —
    changing it would break every ``owner/slug`` reference already sitting
    in someone's state.json."""
    bundle, owner_user = queries.get_bundle_or_404(db, owner, slug)
    orgs.require_write(db, bundle, owner_user, current)

    if payload.title is not None:
        bundle.title = payload.title.strip()
    if payload.description is not None:
        bundle.description = payload.description.strip()
    if payload.kind is not None:
        bundle.kind = payload.kind
    if payload.tags is not None:
        bundle.tags = payload.tags
    db.commit()
    db.refresh(bundle)
    return queries.bundle_detail(db, bundle, owner_user, viewer=current)


@router.delete("/{owner}/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bundle(
    owner: str,
    slug: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Owner (or org publisher) soft-delete. Rows stay so existing imports
    degrade to "gone" rather than orphaning their telemetry, and the slug
    stays taken."""
    bundle, owner_user = queries.get_bundle_or_404(db, owner, slug)
    orgs.require_write(db, bundle, owner_user, current)
    bundle.deleted_at = func.now()
    db.commit()
    log.info("bundle_deleted", ref=f"{owner}/{slug}", by=current.handle)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{owner}/{slug}/versions",
    response_model=VersionDetailOut,
    status_code=status.HTTP_201_CREATED,
)
def publish_version(
    owner: str,
    slug: str,
    payload: VersionPublishIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VersionDetailOut:
    """Publish version N+1. Validation is all-or-nothing.

    ``decode_files`` runs to completion BEFORE any row is written, so a
    payload that trips the secret guard on its last file leaves nothing
    behind — the contract's "never store the payload".

    ``created_by_id`` records the actual person even when the bundle
    belongs to an org, so "who shipped v4" survives a publisher leaving.
    """
    bundle, owner_user = queries.get_bundle_or_404(db, owner, slug)
    orgs.require_write(db, bundle, owner_user, current)

    try:
        files = hub.decode_files([f.model_dump() for f in payload.files])
    except hub.PublishRejected as exc:
        log.info("publish_rejected", ref=f"{owner}/{slug}", error=exc.error, path=exc.path)
        raise _publish_error(exc) from exc

    next_version = bundle.latest_version + 1
    version = BundleVersion(
        bundle_id=bundle.id,
        version=next_version,
        changelog=payload.changelog.strip(),
        pi_manifest=payload.pi_manifest,
        file_count=len(files),
        total_bytes=sum(f.size for f in files),
        created_by_id=current.id,
    )
    db.add(version)
    db.flush()

    for f in files:
        db.add(
            BundleFile(
                version_id=version.id,
                path=f.path,
                content=f.content,
                size=f.size,
                is_text=f.is_text,
            )
        )

    bundle.latest_version = next_version
    # Touch the bundle so `sort=updated` reflects the publish, not the last
    # metadata edit. `onupdate` only fires when a mapped column changes.
    bundle.updated_at = func.now()
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A concurrent publish won the race for this version number. Retry.",
        ) from exc
    db.refresh(version)

    log.info(
        "version_published",
        ref=f"{owner_user.handle}/{bundle.slug}",
        version=next_version,
        files=len(files),
    )
    return VersionDetailOut(
        ref=f"{owner_user.handle}/{bundle.slug}",
        version=queries.version_out(db, version),
        files=queries.file_listing(db, version),
    )


@router.get("/{owner}/{slug}/versions/{version}", response_model=VersionDetailOut)
def get_version(
    owner: str,
    slug: str,
    version: str,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VersionDetailOut:
    """File listing for one version. ``version`` is an int or ``latest``."""
    bundle, owner_user = queries.get_bundle_or_404(db, owner, slug)
    resolved = queries.resolve_version(db, bundle, version)
    return VersionDetailOut(
        ref=f"{owner_user.handle}/{bundle.slug}",
        version=queries.version_out(db, resolved),
        files=queries.file_listing(db, resolved),
    )


@router.get("/{owner}/{slug}/versions/{version}/files", response_model=VersionFilesOut)
def get_version_files(
    owner: str,
    slug: str,
    version: str,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VersionFilesOut:
    """Full contents, base64-encoded — what the phi extension materializes
    an import from. No tar dependency on the client side."""
    bundle, owner_user = queries.get_bundle_or_404(db, owner, slug)
    resolved = queries.resolve_version(db, bundle, version)
    rows = (
        db.execute(
            select(BundleFile).where(BundleFile.version_id == resolved.id).order_by(BundleFile.path)
        )
        .scalars()
        .all()
    )
    return VersionFilesOut(
        ref=f"{owner_user.handle}/{bundle.slug}",
        version=resolved.version,
        pi_manifest=resolved.pi_manifest,
        files=[
            BundleFileContentOut(
                path=f.path,
                size=f.size,
                is_text=f.is_text,
                content_b64=base64.b64encode(f.content).decode("ascii"),
            )
            for f in rows
        ],
    )


@router.get("/{owner}/{slug}/versions/{version}/archive.tgz")
def get_version_archive(
    owner: str,
    slug: str,
    version: str,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """tar.gz of a version, for humans and git-ish consumers."""
    bundle, owner_user = queries.get_bundle_or_404(db, owner, slug)
    resolved = queries.resolve_version(db, bundle, version)
    rows = db.execute(
        select(BundleFile.path, BundleFile.content).where(BundleFile.version_id == resolved.id)
    ).all()
    blob = hub.build_archive(
        owner=owner_user.handle,
        slug=bundle.slug,
        version=resolved.version,
        files=[(p, c) for p, c in rows],
        mtime=resolved.created_at,
    )
    filename = f"{owner_user.handle}-{bundle.slug}-v{resolved.version}.tgz"
    return Response(
        content=blob,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/{owner}/{slug}/events",
    response_model=ImportEventOut,
    status_code=status.HTTP_201_CREATED,
)
def record_import_event(
    owner: str,
    slug: str,
    payload: ImportEventIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportEventOut:
    """Import telemetry. The install count is a fold over this stream, so
    a ``disabled`` event genuinely takes the profile back out of the count."""
    bundle, _owner_user = queries.get_bundle_or_404(db, owner, slug)
    event = queries.record_event(
        db, bundle=bundle, user=current, action=payload.action, version=payload.version
    )
    return ImportEventOut(
        action=event.action,
        version=event.version,
        handle=current.handle,
        display_name=current.profile_display_name,
        created_at=event.created_at,
    )
