"""Profile-, token- and device-shaped request/response schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MeOut(BaseModel):
    """Trimmed user shape returned to the frontend by ``/api/auth/me``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    handle: str
    name: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    is_admin: bool = False
    created_at: datetime
    last_sign_in_at: datetime | None = None


class ProfileOut(BaseModel):
    """A public profile. Everything here is visible to every signed-in
    signed-in user — the hub has no visibility levels (contract → Non-goals)."""

    model_config = ConfigDict(from_attributes=True)

    handle: str
    email: str
    display_name: str
    avatar_url: str | None = None
    created_at: datetime
    bundle_count: int = 0
    import_count: int = 0
    # True for an ORG profile (contract → "Marketplace (v2)"). Nobody signs
    # in as one; its bundles are written by its publishers. The UI keys the
    # marketplace framing and the publisher roster off this.
    is_org: bool = False


class MeUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)

    @field_validator("display_name")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        """An empty box in the UI means "clear it", not "set it to ''" —
        NULL is what makes ``profile_display_name`` fall back to the IdP
        name instead of rendering a blank profile header."""
        if v is None:
            return None
        v = v.strip()
        return v or None


class ApiTokenOut(BaseModel):
    """A token as listed — never the secret, only its display prefix."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class TokenCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def _strip_and_require_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("token name must not be blank")
        return stripped


class TokenCreatedOut(BaseModel):
    """The one and only response that carries a token in the clear."""

    token: str
    api_token: ApiTokenOut


class MeOverviewOut(BaseModel):
    """``GET /api/me`` — profile + token list in one round trip."""

    profile: ProfileOut
    tokens: list[ApiTokenOut]


# ---------------------------------------------------------------- device flow


class DeviceStartIn(BaseModel):
    device_name: str = Field(default="phi CLI", max_length=120)

    @field_validator("device_name")
    @classmethod
    def _default_if_blank(cls, v: str) -> str:
        return v.strip() or "phi CLI"


class DeviceStartOut(BaseModel):
    device_code: str
    user_code: str
    verify_url: str
    interval: int
    expires_in: int


class DevicePollIn(BaseModel):
    device_code: str = Field(min_length=1, max_length=128)


class DevicePollOut(BaseModel):
    """``pending`` → ``approved`` | ``denied`` | ``expired``.

    ``token`` is populated on exactly ONE poll — the first after
    approval. A repeat poll of the same device_code returns ``approved``
    with a null token, so a stolen device_code replayed later is worthless.
    """

    status: str
    token: str | None = None
    profile: ProfileOut | None = None


class DeviceCodeIn(BaseModel):
    user_code: str = Field(min_length=1, max_length=32)


class DevicePendingOut(BaseModel):
    """What ``/connect`` shows the human before they decide."""

    user_code: str
    device_name: str
    status: str
    requested_at: datetime
    expires_at: datetime


class DeviceDecisionOut(BaseModel):
    status: str
    device_name: str
