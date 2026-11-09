"""Dual-channel identity for REST and MCP: app token, then auth adapter.

All bundle read and write routes use get_current_user. An unconfigured
installation denies all bundle access; device start/poll remain anonymous.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth_adapter import InvalidIdentityToken, user_from_headers
from app.config import get_settings
from app.db import get_db
from app.identity import find_or_create_profile, looks_like_app_token, user_for_app_token
from app.models.user import User

logger = logging.getLogger(__name__)


def _bearer(headers: Mapping[str, str]) -> str | None:
    raw = headers.get("authorization") or headers.get("Authorization")
    if not raw:
        return None
    scheme, _, credential = raw.partition(" ")
    if scheme.lower() != "bearer" or not credential:
        return None
    return credential.strip()


def user_from_request_headers(
    db: Session, headers: Mapping[str, str], *, allow_mcp_headers: bool = False
) -> User | None:
    """Shared entry point; only the MCP tool caller may enable edge headers.

    A phk_ bearer always wins. REST and device approval use the default.
    """
    credential = _bearer(headers)
    if credential and looks_like_app_token(credential):
        return user_for_app_token(db, credential)
    identity = user_from_headers(headers, get_settings(), allow_mcp_headers=allow_mcp_headers)
    if identity is None:
        return None
    return find_or_create_profile(
        db, email=identity.email, name=identity.name, avatar_url=identity.picture
    )


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Authenticated profile, or 401; never anonymously expose bundle data."""
    credential = _bearer(request.headers)
    if credential and looks_like_app_token(credential):
        user = user_for_app_token(db, credential)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or revoked API token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user
    try:
        user = user_from_request_headers(db, request.headers)
    except InvalidIdentityToken as exc:
        logger.warning("Identity verification rejected: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """For future personalized, non-bundle responses only."""
    try:
        return user_from_request_headers(db, request.headers)
    except InvalidIdentityToken:
        return None


def require_sso_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Device approval requires a browser identity, never an app-issued token."""
    credential = _bearer(request.headers)
    if credential and looks_like_app_token(credential):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Device approval requires a browser session, not an API token.",
        )
    return get_current_user(request, db)
