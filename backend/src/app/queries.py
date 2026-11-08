"""Read models shared by the REST routers and the MCP tools.

Every "shape a bundle for output" decision lives here exactly once, so
``GET /api/bundles/sample/routing`` and ``hub_get_bundle('sample/routing')``
can never disagree about install counts, version ordering, or which
snippets a caller is handed.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, func, select, tuple_
from sqlalchemy.orm import Session, aliased

from app import hub, orgs
from app.config import get_settings
from app.models.bundle import (
    INSTALLED_ACTIONS,
    Bundle,
    BundleFile,
    BundleVersion,
    ImportEvent,
)
from app.models.user import User
from app.schemas.bundle import (
    BundleDetailOut,
    BundleFileOut,
    BundleSummaryOut,
    BundleVersionOut,
    ImportEventOut,
    ImportSnippetsOut,
)
from app.schemas.org import BundleUpdateOut, UpdateCheckOut
from app.schemas.user import ProfileOut

# How many recent import events the detail view carries. Enough to show
# "who picked this up lately" without turning the page into a log viewer.
RECENT_EVENT_LIMIT = 20

SORT_UPDATED = "updated"
SORT_POPULAR = "popular"


def profile_out(db: Session, user: User) -> ProfileOut:
    """A profile with its two counters.

    ``bundle_count`` excludes soft-deleted bundles; ``import_count`` is how
    many bundles *this* profile currently holds, which is the mirror image
    of a bundle's install count and uses the same last-event-wins rule.
    """
    bundle_count = (
        db.execute(
            select(func.count())
            .select_from(Bundle)
            .where(Bundle.owner_id == user.id, Bundle.deleted_at.is_(None))
        ).scalar_one()
        or 0
    )
    # Same last-event-wins fold as a bundle's install count, but sliced to
    # one profile: "how many bundles is this person currently holding".
    ranked = (
        select(
            ImportEvent.action.label("action"),
            func.row_number()
            .over(
                partition_by=ImportEvent.bundle_id,
                order_by=(ImportEvent.created_at.desc(), ImportEvent.id.desc()),
            )
            .label("rn"),
        )
        .where(ImportEvent.user_id == user.id)
        .subquery("my_events")
    )
    import_count = (
        db.execute(
            select(func.count())
            .select_from(ranked)
            .where(ranked.c.rn == 1, ranked.c.action.in_(INSTALLED_ACTIONS))
        ).scalar_one()
        or 0
    )
    return ProfileOut(
        handle=user.handle,
        email=user.email,
        display_name=user.profile_display_name,
        avatar_url=user.avatar_url,
        created_at=user.created_at,
        bundle_count=bundle_count,
        import_count=import_count,
        is_org=user.is_org,
    )


def _summary(bundle: Bundle, owner: User, installs: int) -> BundleSummaryOut:
    return BundleSummaryOut(
        id=bundle.id,
        ref=f"{owner.handle}/{bundle.slug}",
        owner=owner.handle,
        owner_display_name=owner.profile_display_name,
        slug=bundle.slug,
        title=bundle.title,
        description=bundle.description,
        kind=bundle.kind,
        tags=list(bundle.tags or []),
        latest_version=bundle.latest_version,
        install_count=installs,
        created_at=bundle.created_at,
        updated_at=bundle.updated_at,
    )


def bundle_summary(db: Session, bundle: Bundle, owner: User) -> BundleSummaryOut:
    """Card shape for a single known bundle (create / publish responses)."""
    return _summary(bundle, owner, install_count(db, bundle.id))


def search_bundles(
    db: Session,
    *,
    q: str | None = None,
    kind: str | None = None,
    owner: str | None = None,
    org_only: bool = False,
    sort: str = SORT_UPDATED,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[BundleSummaryOut], int]:
    """Browse / search. Reads are org-wide; soft-deleted bundles are hidden.

    ``sort=popular`` orders by install count (ties broken by recency) —
    the list join is a LEFT OUTER so a never-imported bundle still shows
    up with a zero rather than dropping out of the results.

    ``org_only`` narrows to bundles owned by an ORG profile — the curated
    marketplace view. It rides the ``users`` join the query already makes,
    so it costs nothing beyond the predicate.
    """
    installs = hub.install_count_subquery()
    stmt = (
        select(Bundle, User, func.coalesce(installs.c.installs, 0).label("installs"))
        .join(User, Bundle.owner_id == User.id)
        .outerjoin(installs, installs.c.bundle_id == Bundle.id)
        .where(Bundle.deleted_at.is_(None))
    )
    stmt = hub.apply_search(stmt, q)
    if kind:
        stmt = stmt.where(Bundle.kind == kind)
    if owner:
        stmt = stmt.where(User.handle == owner)
    if org_only:
        stmt = stmt.where(User.is_org.is_(True))

    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()

    if sort == SORT_POPULAR:
        stmt = stmt.order_by(func.coalesce(installs.c.installs, 0).desc(), Bundle.updated_at.desc())
    else:
        stmt = stmt.order_by(Bundle.updated_at.desc())

    rows = db.execute(stmt.limit(limit).offset(offset)).all()
    return [_summary(b, u, n) for b, u, n in rows], total


def install_count(db: Session, bundle_id: UUID) -> int:
    installs = hub.install_count_subquery()
    return (
        db.execute(
            select(installs.c.installs).where(installs.c.bundle_id == bundle_id)
        ).scalar_one_or_none()
        or 0
    )


def find_bundle(db: Session, owner_handle: str, slug: str) -> tuple[Bundle, User] | None:
    row = db.execute(
        select(Bundle, User)
        .join(User, Bundle.owner_id == User.id)
        .where(
            func.lower(User.handle) == (owner_handle or "").lower(),
            func.lower(Bundle.slug) == (slug or "").lower(),
            Bundle.deleted_at.is_(None),
        )
    ).first()
    return (row[0], row[1]) if row else None


def get_bundle_or_404(db: Session, owner_handle: str, slug: str) -> tuple[Bundle, User]:
    found = find_bundle(db, owner_handle, slug)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No bundle '{owner_handle}/{slug}'.",
        )
    return found


def parse_ref(ref: str) -> tuple[str, str]:
    """Split an ``owner/slug`` reference. Tolerates a ``@vN`` suffix by
    ignoring it — callers that care about the version take it as its own
    argument, and silently accepting the fuller form means a user pasting
    ``sample/routing@v3`` into a tool call gets the bundle instead of a
    parse error."""
    cleaned = (ref or "").strip().strip("/")
    cleaned = cleaned.split("@", 1)[0]
    parts = [p for p in cleaned.split("/") if p]
    if len(parts) != 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bundle references look like 'owner/slug' (got {ref!r}).",
        )
    return parts[0], parts[1]


def resolve_version(db: Session, bundle: Bundle, version: str | int) -> BundleVersion:
    """Resolve ``latest`` or an integer to a concrete version row."""
    stmt = select(BundleVersion).where(BundleVersion.bundle_id == bundle.id)
    if isinstance(version, str) and version.lower() == "latest":
        stmt = stmt.order_by(BundleVersion.version.desc()).limit(1)
    else:
        try:
            wanted = int(version)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Version must be an integer or 'latest'.",
            ) from None
        stmt = stmt.where(BundleVersion.version == wanted)
    found = db.execute(stmt).scalar_one_or_none()
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No version {version} of '{bundle.slug}'.",
        )
    return found


def version_out(db: Session, version: BundleVersion) -> BundleVersionOut:
    author = (
        db.execute(select(User.handle).where(User.id == version.created_by_id)).scalar_one_or_none()
        if version.created_by_id
        else None
    )
    return BundleVersionOut(
        version=version.version,
        changelog=version.changelog,
        file_count=version.file_count,
        total_bytes=version.total_bytes,
        created_at=version.created_at,
        created_by=author,
        pi_manifest=version.pi_manifest,
    )


def version_list(db: Session, bundle: Bundle) -> list[BundleVersionOut]:
    """Newest first — the changelog reads top-down like a release feed."""
    versions = (
        db.execute(
            select(BundleVersion)
            .where(BundleVersion.bundle_id == bundle.id)
            .order_by(BundleVersion.version.desc())
        )
        .scalars()
        .all()
    )
    return [version_out(db, v) for v in versions]


def file_listing(db: Session, version: BundleVersion) -> list[BundleFileOut]:
    rows = db.execute(
        select(BundleFile.path, BundleFile.size, BundleFile.is_text)
        .where(BundleFile.version_id == version.id)
        .order_by(BundleFile.path)
    ).all()
    return [BundleFileOut(path=p, size=s, is_text=t) for p, s, t in rows]


def recent_events(db: Session, bundle: Bundle) -> list[ImportEventOut]:
    rows = db.execute(
        select(ImportEvent, User)
        .join(User, ImportEvent.user_id == User.id)
        .where(ImportEvent.bundle_id == bundle.id)
        .order_by(ImportEvent.created_at.desc(), ImportEvent.id.desc())
        .limit(RECENT_EVENT_LIMIT)
    ).all()
    return [
        ImportEventOut(
            action=e.action,
            version=e.version,
            handle=u.handle,
            display_name=u.profile_display_name,
            created_at=e.created_at,
        )
        for e, u in rows
    ]


def bundle_detail(
    db: Session, bundle: Bundle, owner: User, *, viewer: User | None
) -> BundleDetailOut:
    """The full detail payload — one shape for the web page and MCP alike."""
    base_url = get_settings().public_base_url
    versions = version_list(db, bundle)
    files: list[BundleFileOut] = []
    effective_version = bundle.latest_version or 1
    if bundle.latest_version:
        latest = resolve_version(db, bundle, "latest")
        files = file_listing(db, latest)
        effective_version = latest.version

    return BundleDetailOut(
        bundle=_summary(bundle, owner, install_count(db, bundle.id)),
        owner_profile=profile_out(db, owner),
        versions=versions,
        files=files,
        recent_events=recent_events(db, bundle),
        import_snippets=ImportSnippetsOut(
            **hub.import_snippets(
                base_url=base_url,
                owner=owner.handle,
                slug=bundle.slug,
                version=effective_version,
            )
        ),
        import_instructions=hub.import_instructions(
            base_url=base_url,
            owner=owner.handle,
            slug=bundle.slug,
            version=effective_version,
        ),
        is_owner=bool(viewer and viewer.id == owner.id),
        # Kept SEPARATE from ``is_owner``: for an org-owned bundle the two
        # differ, and the UI needs the write question ("show Edit/Delete")
        # answered without re-deriving the publisher rule client-side.
        # ``is_owner`` stays literally "you are the owner" so nothing built
        # against it changes meaning.
        can_write=bool(viewer and orgs.can_write(db, bundle, owner, viewer)),
    )


def check_updates(db: Session, installed: list[tuple[str, int]]) -> UpdateCheckOut:
    """Which of a client's installed bundles have moved on (contract →
    "Update checking").

    One round trip for the whole hub, however many bundles it holds. Two
    things make that possible:

    - the refs go in as a single ``(lower(handle), lower(slug)) IN (...)``
      tuple predicate rather than one query per ref;
    - the latest version's changelog is picked up by an outer join keyed on
      ``BundleVersion.version == Bundle.latest_version``, so "what changed"
      needs no second pass.

    Classification, per the contract:

    - **update** — the bundle resolves and its ``latest_version`` is
      strictly ahead of what the client holds.
    - **missing** — the ref does not resolve. Soft-deleted bundles land
      here (they are gone as far as any client is concerned), and so do
      malformed refs: this is a batch endpoint, and one fat-fingered entry
      must not 400 an entire startup check.
    - **current** — everything else. Appears in neither list; a client that
      is fully up to date gets two empty arrays.
    """
    wanted: dict[tuple[str, str], int] = {}
    order: list[str] = []
    missing: list[str] = []

    for raw_ref, version in installed:
        ref = (raw_ref or "").strip()
        if ref not in order:
            order.append(ref)
        cleaned = ref.strip("/").split("@", 1)[0]
        parts = [p for p in cleaned.split("/") if p]
        if len(parts) != 2:
            if ref not in missing:
                missing.append(ref)
            continue
        key = (parts[0].lower(), parts[1].lower())
        # A duplicate ref keeps the LOWEST version seen: if a client somehow
        # reports the same bundle twice, the stale copy is the one that
        # needs telling about.
        wanted[key] = min(wanted.get(key, version), version)

    updates: list[BundleUpdateOut] = []
    if wanted:
        latest = aliased(BundleVersion)
        rows = db.execute(
            select(Bundle, User, latest.changelog)
            .join(User, Bundle.owner_id == User.id)
            .outerjoin(
                latest,
                and_(latest.bundle_id == Bundle.id, latest.version == Bundle.latest_version),
            )
            .where(
                Bundle.deleted_at.is_(None),
                tuple_(func.lower(User.handle), func.lower(Bundle.slug)).in_(list(wanted)),
            )
        ).all()

        found: set[tuple[str, str]] = set()
        for bundle, owner, changelog in rows:
            key = (owner.handle.lower(), bundle.slug.lower())
            found.add(key)
            held = wanted.get(key, 0)
            if bundle.latest_version > held:
                updates.append(
                    BundleUpdateOut(
                        ref=f"{owner.handle}/{bundle.slug}",
                        latest_version=bundle.latest_version,
                        kind=bundle.kind,
                        title=bundle.title,
                        changelog=changelog or "",
                        is_org=owner.is_org,
                    )
                )

        # Anything asked for that the query did not return is gone.
        for key in wanted:
            if key not in found:
                ref = f"{key[0]}/{key[1]}"
                if ref not in missing:
                    missing.append(ref)

    # Stable, caller-visible ordering: highest available version first, then
    # by ref, so a client rendering "N updates" always lists them the same way.
    updates.sort(key=lambda u: (-(u.latest_version), u.ref))
    return UpdateCheckOut(updates=updates, missing=missing, checked=len(order))


def record_event(
    db: Session, *, bundle: Bundle, user: User, action: str, version: int | None
) -> ImportEvent:
    event = ImportEvent(bundle_id=bundle.id, user_id=user.id, action=action, version=version)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def as_tool_dict(model: Any) -> Any:
    """Pydantic → plain JSON-able dict, for MCP tool returns.

    ``mode="json"`` matters: MCP serializes tool results to JSON, and the
    default python mode would hand it ``UUID`` / ``datetime`` objects it
    can't encode.
    """
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    if isinstance(model, list):
        return [as_tool_dict(m) for m in model]
    return model
