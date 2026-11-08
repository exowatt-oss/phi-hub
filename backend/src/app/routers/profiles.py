"""Profiles, the signed-in user's own view, and manual token management.

Reads are org-wide (every signed-in user sees every profile and
every bundle); writes are scoped to your own row.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.identity import new_token
from app.models.token import ApiToken
from app.models.user import User
from app.queries import profile_out, search_bundles
from app.schemas.bundle import ProfileDetailOut
from app.schemas.user import (
    ApiTokenOut,
    MeOverviewOut,
    MeUpdateIn,
    ProfileOut,
    TokenCreatedOut,
    TokenCreateIn,
)

router = APIRouter(tags=["profiles"])
log = structlog.get_logger("profiles")


def _my_tokens(db: Session, user: User) -> list[ApiTokenOut]:
    """Active tokens first, newest first within each group — a revoked
    token stays listed (as an audit trail) but never at the top."""
    rows = (
        db.execute(
            select(ApiToken)
            .where(ApiToken.user_id == user.id)
            .order_by(ApiToken.revoked_at.is_(None).desc(), ApiToken.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [ApiTokenOut.model_validate(t) for t in rows]


@router.get("/me", response_model=MeOverviewOut)
def get_me(
    current: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> MeOverviewOut:
    """Your profile plus your tokens (prefixes only — never the secret)."""
    return MeOverviewOut(profile=profile_out(db, current), tokens=_my_tokens(db, current))


@router.patch("/me", response_model=MeOverviewOut)
def patch_me(
    payload: MeUpdateIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeOverviewOut:
    """Edit your display name. The handle is immutable — it is baked into
    every ``owner/slug`` reference anyone has already imported."""
    current.display_name = payload.display_name
    db.commit()
    db.refresh(current)
    return MeOverviewOut(profile=profile_out(db, current), tokens=_my_tokens(db, current))


@router.post("/tokens", response_model=TokenCreatedOut, status_code=status.HTTP_201_CREATED)
def create_token(
    payload: TokenCreateIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TokenCreatedOut:
    """Mint a token by hand (the UI's escape hatch from the device flow).

    The plaintext is in this response and nowhere else — the DB only ever
    holds its sha256.
    """
    plaintext, prefix, token_hash = new_token()
    token = ApiToken(
        user_id=current.id, name=payload.name.strip(), prefix=prefix, token_hash=token_hash
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    log.info("token_minted", handle=current.handle, prefix=prefix)
    return TokenCreatedOut(token=plaintext, api_token=ApiTokenOut.model_validate(token))


@router.delete("/tokens/{token_id}", response_model=ApiTokenOut)
def revoke_token(
    token_id: UUID,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiTokenOut:
    """Revoke one of your tokens. Idempotent — re-revoking is a no-op."""
    token = db.execute(
        select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == current.id)
    ).scalar_one_or_none()
    if token is None:
        # 404 rather than 403 for someone else's token id: whether a token
        # exists isn't information a non-owner should get.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such token.")
    if token.revoked_at is None:
        token.revoked_at = func.now()
        db.commit()
        db.refresh(token)
        log.info("token_revoked", handle=current.handle, prefix=token.prefix)
    return ApiTokenOut.model_validate(token)


@router.get("/profiles", response_model=list[ProfileOut])
def list_profiles(
    _: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ProfileOut]:
    """The directory, with per-profile bundle and import counts."""
    users = db.execute(select(User).order_by(User.handle)).scalars().all()
    return [profile_out(db, u) for u in users]


@router.get("/profiles/{handle}", response_model=ProfileDetailOut)
def get_profile(
    handle: str,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileDetailOut:
    """One profile plus everything they publish."""
    user = db.execute(
        select(User).where(func.lower(User.handle) == handle.lower())
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No profile '{handle}'.")
    bundles, _total = search_bundles(db, owner=user.handle)
    return ProfileDetailOut(profile=profile_out(db, user), bundles=bundles)
