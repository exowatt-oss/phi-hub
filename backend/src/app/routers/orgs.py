"""Org publisher roster (contract → "Marketplace (v2)" → org profiles).

Reads are org-wide like everything else in the hub: any signed-in user can
see who publishes for an org, because that is the answer to "should I
trust this bundle". Writes are publishers-only — the roster is
self-governing, with no separate admin role, which is why the ≥1-publisher
invariant in ``app.orgs.remove_publisher`` matters so much.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import orgs, queries
from app.db import get_db
from app.deps import get_current_user
from app.models.user import User
from app.schemas.org import OrgSummaryOut, PublisherAddIn, PublisherListOut, PublisherOut

router = APIRouter(prefix="/orgs", tags=["orgs"])


def _require_publisher(db: Session, org: User, current: User) -> None:
    if not orgs.is_publisher(db, org.id, current.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Only a publisher for '{org.handle}' can change its publisher list.",
        )


def _roster(db: Session, org: User, viewer: User) -> PublisherListOut:
    """The response every endpoint in this module returns.

    Uniform on purpose: an add or a remove hands back the *new* roster, so
    the UI never has to follow a mutation with a re-fetch to render the
    result of what it just did.
    """
    rows = orgs.publisher_rows(db, org)
    return PublisherListOut(
        org=queries.profile_out(db, org),
        publishers=[
            PublisherOut(
                handle=user.handle,
                display_name=user.profile_display_name,
                email=user.email,
                avatar_url=user.avatar_url,
                added_at=grant.created_at,
                added_by=added_by,
                # Never signed in — the profile exists only because someone
                # invited them by email. Worth showing: an invite typo'd
                # into a nonexistent address looks exactly like this forever.
                pending=user.last_sign_in_at is None,
            )
            for grant, user, added_by in rows
        ],
        is_publisher=any(user.id == viewer.id for _grant, user, _by in rows),
    )


@router.get("", response_model=list[OrgSummaryOut])
def list_orgs(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[OrgSummaryOut]:
    """Every org profile, with whether the CALLER may publish for it.

    Exists so a client can offer "publish as <org>" without hard-coding an
    org handle or probing the roster endpoint once per org. There are one
    or two orgs, so this is a fixed two-query page, not a listing.
    """
    mine = orgs.publisher_org_ids(db, current.id)
    return [
        OrgSummaryOut(
            profile=queries.profile_out(db, org),
            is_publisher=org.id in mine,
            publisher_count=orgs.publisher_count(db, org.id),
        )
        for org in orgs.list_orgs(db)
    ]


@router.get("/{handle}/publishers", response_model=PublisherListOut)
def list_publishers(
    handle: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PublisherListOut:
    """Who may publish under this org. Any authenticated user may read it.

    ``is_publisher`` answers "may *I* change this list" in the same round
    trip, so the UI does not re-derive the rule client-side.
    """
    org = orgs.get_org_or_404(db, handle)
    return _roster(db, org, current)


@router.post(
    "/{handle}/publishers",
    response_model=PublisherListOut,
    status_code=status.HTTP_201_CREATED,
)
def add_publisher(
    handle: str,
    payload: PublisherAddIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PublisherListOut:
    """Grant publish rights by email, creating the profile stub if needed.

    Publishers only (403 otherwise). Adding someone who is already a
    publisher is a no-op rather than an error — the roster you get back is
    the same either way, which makes the call safely retryable.
    """
    org = orgs.get_org_or_404(db, handle)
    _require_publisher(db, org, current)
    orgs.add_publisher_by_email(db, org=org, email=payload.email, added_by=current)
    return _roster(db, org, current)


@router.delete("/{handle}/publishers/{user_handle}", response_model=PublisherListOut)
def remove_publisher(
    handle: str,
    user_handle: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PublisherListOut:
    """Revoke publish rights. Publishers only; the last one cannot go (409).

    Removing yourself is allowed as long as someone else is left — an org
    with an empty roster owns bundles nobody can ever update, and the app
    has no way back from that.
    """
    org = orgs.get_org_or_404(db, handle)
    _require_publisher(db, org, current)
    orgs.remove_publisher(db, org=org, handle=user_handle, removed_by=current)
    return _roster(db, org, current)
