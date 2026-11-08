"""Identity-adjacent API; sign-in lives outside this application."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.config import get_settings
from app.deps import get_current_user
from app.models.user import User
from app.schemas.user import MeOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=MeOut)
def me(current: User = Depends(get_current_user)) -> User:
    return current


@router.get("/logout")
def logout(request: Request) -> dict[str, str]:
    """The operator-configured IdP logout URL or this instance's base URL.

    Without AUTH_LOGOUT_URL no edge session is cleared. A production operator
    must configure the IdP logout endpoint if browser logout is required.
    """
    settings = get_settings()
    target = settings.auth_logout_url or settings.app_base_url or str(request.base_url)
    return {"logout_url": target}
