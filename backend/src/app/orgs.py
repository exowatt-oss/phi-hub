"""Marketplace domain logic: org profiles, publishers, publish authorization.

Surface-agnostic, like ``hub.py`` and ``queries.py`` — the REST routers and
the MCP tools both come through here so "who may write under this owner"
has exactly one answer in the codebase.

The model in one paragraph: an org profile is a ``users`` row with
``is_org = true`` that nobody can sign in as. ``org_publishers`` names the
people allowed to publish under it. Every owner-scoped bundle write asks
:func:`can_write`, which is "you are the owner" OR "you are a publisher of
the owning org" — the marketplace's entire trust model, and the same one
Claude's plugin marketplaces use (publishers gate what ships; consumers
trust the org, not each individual).
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.identity import find_or_create_stub_profile
from app.models.bundle import Bundle
from app.models.org import OrgPublisher
from app.models.user import User

log = structlog.get_logger("orgs")


# --------------------------------------------------------------------------
# Lookup
# --------------------------------------------------------------------------


def find_org(db: Session, handle: str) -> User | None:
    """The org profile with this handle, or ``None``.

    A handle belonging to a *person* returns ``None``: callers use this to
    answer "may I publish under `owner`", and a human handle is never a
    valid answer to that question for anyone but its owner.
    """
    return db.execute(
        select(User).where(
            func.lower(User.handle) == (handle or "").strip().lower(),
            User.is_org.is_(True),
        )
    ).scalar_one_or_none()


def get_org_or_404(db: Session, handle: str) -> User:
    org = find_org(db, handle)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No org '{handle}'.",
        )
    return org


def list_orgs(db: Session) -> list[User]:
    """Every org profile, by handle. There are one or two of these, ever."""
    return (
        db.execute(select(User).where(User.is_org.is_(True)).order_by(User.handle)).scalars().all()
    )


def publisher_org_ids(db: Session, user_id: UUID) -> set[UUID]:
    """Every org this user may publish for — one query, not one per org."""
    return {
        row[0]
        for row in db.execute(
            select(OrgPublisher.org_id).where(OrgPublisher.user_id == user_id)
        ).all()
    }


def is_publisher(db: Session, org_id: UUID, user_id: UUID) -> bool:
    return (
        db.execute(
            select(OrgPublisher.id).where(
                OrgPublisher.org_id == org_id, OrgPublisher.user_id == user_id
            )
        ).first()
        is not None
    )


def can_write(db: Session, bundle: Bundle, owner: User, current: User) -> bool:
    """May ``current`` write to this bundle?

    Owner, or a publisher of the owning org. The org check is skipped
    entirely for personally-owned bundles, so the common path stays a
    single ``UUID`` comparison with no extra query.
    """
    if bundle.owner_id == current.id:
        return True
    if not owner.is_org:
        return False
    return is_publisher(db, owner.id, current.id)


def require_write(db: Session, bundle: Bundle, owner: User, current: User) -> None:
    """:func:`can_write` or 403.

    The message names both routes to access on purpose: a publisher who was
    just removed from the org, and a colleague who assumed the org was
    open, need different fixes and the same 403 has to point at both.
    """
    if can_write(db, bundle, owner, current):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "Only the bundle's owner, or a publisher of the owning org, can change it."
            if owner.is_org
            else "Only the bundle's owner can change it."
        ),
    )


def resolve_publish_owner(db: Session, current: User, owner: str | None) -> User:
    """Which profile a ``POST /api/bundles`` should publish under.

    ``owner`` omitted (or the caller's own handle) → the caller. Otherwise
    it must be an org handle the caller publishes for: 404 if there is no
    such org, 403 if they are not one of its publishers (contract →
    "Publishing to an org").
    """
    wanted = (owner or "").strip()
    if not wanted or wanted.lower() == current.handle.lower():
        return current
    org = get_org_or_404(db, wanted)
    if not is_publisher(db, org.id, current.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"You are not a publisher for '{org.handle}'. "
                f"An existing publisher can add you at /p/{org.handle}."
            ),
        )
    return org


# --------------------------------------------------------------------------
# Publisher roster
# --------------------------------------------------------------------------


def publisher_rows(db: Session, org: User) -> list[tuple[OrgPublisher, User, str | None]]:
    """``(grant, publisher, added_by_handle)`` for an org, oldest grant first.

    Oldest first so the roster reads as a history — the person who
    bootstrapped the org stays at the top instead of shuffling as people
    are added.
    """
    # Two joins to ``users`` in one statement (the publisher, and whoever
    # granted them) — the second needs its own alias or the ORM cannot tell
    # the two apart.
    added_by = select(User.id, User.handle).subquery("added_by")
    rows = db.execute(
        select(OrgPublisher, User, added_by.c.handle)
        .join(User, OrgPublisher.user_id == User.id)
        .outerjoin(added_by, added_by.c.id == OrgPublisher.added_by_id)
        .where(OrgPublisher.org_id == org.id)
        .order_by(OrgPublisher.created_at.asc(), OrgPublisher.id.asc())
    ).all()
    return [(grant, user, handle) for grant, user, handle in rows]


def publisher_count(db: Session, org_id: UUID) -> int:
    return (
        db.execute(
            select(func.count()).select_from(OrgPublisher).where(OrgPublisher.org_id == org_id)
        ).scalar_one()
        or 0
    )


def grant(db: Session, *, org: User, user: User, added_by: User | None) -> OrgPublisher | None:
    """Grant publish rights. Returns ``None`` if they already had them.

    Does not commit — callers batch.
    """
    existing = db.execute(
        select(OrgPublisher).where(OrgPublisher.org_id == org.id, OrgPublisher.user_id == user.id)
    ).scalar_one_or_none()
    if existing is not None:
        return None
    row = OrgPublisher(
        org_id=org.id,
        user_id=user.id,
        added_by_id=added_by.id if added_by is not None else None,
    )
    db.add(row)
    db.flush()
    return row


def add_publisher_by_email(db: Session, *, org: User, email: str, added_by: User) -> User:
    """Add a publisher by email, creating their profile stub if needed.

    Contract: "adding an email with no profile yet auto-creates the profile
    stub". That is what makes onboarding a new publisher a single call —
    you should not have to make someone visit the hub before you can grant
    them anything.
    """
    normalized = (email or "").strip().lower()
    if not normalized or "@" not in normalized:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide the publisher's email address.",
        )
    user = find_or_create_stub_profile(db, email=normalized)
    if user.is_org:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="An org profile cannot be a publisher.",
        )
    created = grant(db, org=org, user=user, added_by=added_by)
    db.commit()
    if created is not None:
        log.info("publisher_added", org=org.handle, publisher=user.handle, by=added_by.handle)
    return user


def remove_publisher(db: Session, *, org: User, handle: str, removed_by: User) -> None:
    """Revoke publish rights, refusing to empty the roster.

    The ≥1 invariant is not bureaucracy: an org with no publishers owns
    bundles nobody can ever update or delete, and there is no admin path to
    fix it from inside the app. Removing yourself is allowed — leaving is a
    normal thing to do — as long as someone is left holding the keys.
    """
    target = db.execute(
        select(User).where(func.lower(User.handle) == (handle or "").strip().lower())
    ).scalar_one_or_none()
    row = (
        db.execute(
            select(OrgPublisher).where(
                OrgPublisher.org_id == org.id, OrgPublisher.user_id == target.id
            )
        ).scalar_one_or_none()
        if target is not None
        else None
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{handle}' is not a publisher for '{org.handle}'.",
        )
    if publisher_count(db, org.id) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"'{org.handle}' must keep at least one publisher. "
                "Add a replacement before removing the last one."
            ),
        )
    db.delete(row)
    db.commit()
    log.info("publisher_removed", org=org.handle, publisher=handle, by=removed_by.handle)


# --------------------------------------------------------------------------
# Seed
# --------------------------------------------------------------------------


def ensure_seed(db: Session) -> User:
    """Create the configured org profile and its initial publishers, once.

    Idempotent and safe to run on every boot and from every worker:

    - the org row is found by email before it is created;
    - each publisher stub is found by email before it is created;
    - each grant is checked against the ``(org_id, user_id)`` unique
      constraint before insert.

    Deliberately **additive only**. Dropping an email from
    ``ORG_INITIAL_PUBLISHERS`` does not revoke that person — revocation is
    an explicit ``DELETE /api/orgs/{handle}/publishers/{handle}`` so a
    typo'd env var can never silently lock the org's publishers out. The
    seed also never touches an org that already has publishers beyond
    re-asserting the configured ones.

    Returns the org profile. Raises nothing the caller need catch other
    than genuine DB failures — ``main._lifespan`` logs and continues, so a
    seed problem degrades the marketplace rather than the whole app.
    """
    settings = get_settings()
    if not all(
        (
            settings.org_handle,
            settings.org_display_name,
            settings.org_email,
            settings.initial_publisher_emails,
        )
    ):
        raise ValueError("Org seeding requires handle, name, email and initial publishers")
    org = db.execute(
        select(User).where(User.email == settings.org_email.lower())
    ).scalar_one_or_none()
    created_org = org is None
    if org is None:
        org = find_or_create_stub_profile(
            db,
            email=settings.org_email.lower(),
            name=settings.org_display_name,
            is_org=True,
        )
        # Use the configured org handle, not the email local-part.
        org.handle = _available_handle(db, settings.org_handle, org.id)
        org.display_name = settings.org_display_name
        org.is_org = True
        db.flush()
    elif not org.is_org:
        # Somebody's sign-in got there first with this address. Refuse to
        # convert a real person's profile into an org — that would silently
        # hand their bundles to the org's publishers.
        db.rollback()
        raise RuntimeError(
            f"ORG_EMAIL {settings.org_email!r} already belongs to a non-org profile "
            f"('{org.handle}'). Point ORG_EMAIL at an address no person signs in with."
        )

    granted: list[str] = []
    for email in settings.initial_publisher_emails:
        user = find_or_create_stub_profile(db, email=email)
        if grant(db, org=org, user=user, added_by=None) is not None:
            granted.append(user.handle)

    db.commit()
    db.refresh(org)
    if created_org or granted:
        log.info("org_seeded", org=org.handle, created=created_org, granted=granted)
    return org


def _available_handle(db: Session, wanted: str, self_id: UUID) -> str:
    """``wanted`` unless another row already holds it, in which case keep
    whatever the caller already has. The org handle is configuration, and
    silently suffixing it would break existing owner/slug references."""
    clash = db.execute(
        select(User.id).where(func.lower(User.handle) == wanted.lower(), User.id != self_id)
    ).first()
    if clash is None:
        return wanted
    current = db.execute(select(User.handle).where(User.id == self_id)).scalar_one()
    log.warning("org_handle_taken", wanted=wanted, using=current)
    return current
