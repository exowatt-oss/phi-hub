"""Device authorization — how `phi` gets a token without copy-pasting a secret.

The shape is OAuth's device flow, trimmed to what the hub needs
(``README.md`` → "Device flow"):

    CLI                          hub                        browser
     |-- POST /device/start ----->|
     |<-- device_code, user_code -|
     |                            |<-- GET /device/pending?code= (SSO)
     |                            |<-- POST /device/approve      (SSO)
     |-- POST /device/poll ------>|
     |<-- phk_... (once) ---------|

``start`` and ``poll`` are on the manifest's
``allow_paths_unauthenticated`` list — the CLI has no credential yet, and
neither endpoint mints anything on its own. ``pending`` / ``approve`` /
``deny`` require a *browser* session specifically (``require_sso_user``):
if an API token could approve a device request, a leaked token could mint
its own replacements and revocation would stop meaning anything.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import require_sso_user
from app.identity import hash_token, new_token, new_user_code, normalize_user_code
from app.models.token import (
    DEVICE_APPROVED,
    DEVICE_DENIED,
    DEVICE_EXPIRED,
    DEVICE_PENDING,
    ApiToken,
    DeviceAuth,
)
from app.models.user import User
from app.queries import profile_out
from app.schemas.user import (
    DeviceCodeIn,
    DeviceDecisionOut,
    DevicePendingOut,
    DevicePollIn,
    DevicePollOut,
    DeviceStartIn,
    DeviceStartOut,
)

router = APIRouter(prefix="/auth/device", tags=["device"])
log = structlog.get_logger("device")

# How many times to retry user-code generation on a unique collision.
# With 32^8 (~1.1e12) codes and a 15-minute window the odds are nil, but
# a bounded retry beats a 500.
_CODE_ATTEMPTS = 8


def _now() -> datetime:
    return datetime.now(UTC)


def _expire_if_needed(db: Session, device: DeviceAuth) -> DeviceAuth:
    """Lazily settle a timed-out request.

    There is no sweeper job: a request only matters when someone looks at
    it, so the expiry check happens on read. That also means an approval
    that lands one second late is correctly refused rather than honored
    because no cron had run yet.
    """
    if device.status == DEVICE_PENDING and device.expires_at <= _now():
        device.status = DEVICE_EXPIRED
        device.token_plaintext = None
        db.commit()
        db.refresh(device)
    return device


def _load_by_user_code(db: Session, user_code: str) -> DeviceAuth:
    code = normalize_user_code(user_code)
    device = db.execute(select(DeviceAuth).where(DeviceAuth.user_code == code)).scalar_one_or_none()
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That code doesn't match any pending device. Check it and try again.",
        )
    return _expire_if_needed(db, device)


@router.post("/start", response_model=DeviceStartOut)
def device_start(payload: DeviceStartIn, db: Session = Depends(get_db)) -> DeviceStartOut:
    """Begin a device authorization. Unauthenticated by design."""
    settings = get_settings()
    device_code, _, device_code_hash = new_token()
    expires_at = _now() + timedelta(seconds=settings.device_code_ttl_seconds)

    for _ in range(_CODE_ATTEMPTS):
        device = DeviceAuth(
            device_code_hash=device_code_hash,
            user_code=new_user_code(),
            device_name=payload.device_name,
            status=DEVICE_PENDING,
            expires_at=expires_at,
        )
        db.add(device)
        try:
            db.commit()
            break
        except IntegrityError:
            # Either the user_code collided (retry) or the device_code did
            # (impossible in practice; a retry regenerates neither, so bail
            # to the generic error below rather than spinning).
            db.rollback()
    else:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not allocate a device code. Try again.",
        )

    db.refresh(device)
    log.info("device_start", device_name=device.device_name, device_id=str(device.id))
    return DeviceStartOut(
        device_code=device_code,
        user_code=device.user_code,
        verify_url=f"{settings.public_base_url}/connect?code={device.user_code}",
        interval=settings.device_poll_interval_seconds,
        expires_in=settings.device_code_ttl_seconds,
    )


@router.post("/poll", response_model=DevicePollOut)
def device_poll(payload: DevicePollIn, db: Session = Depends(get_db)) -> DevicePollOut:
    """Poll for the outcome. Unauthenticated — the device_code IS the credential.

    An unknown device_code returns ``expired`` rather than 404: a polling
    CLI should stop, and telling an anonymous caller "that code doesn't
    exist" would let someone enumerate live codes.
    """
    device = db.execute(
        select(DeviceAuth).where(DeviceAuth.device_code_hash == hash_token(payload.device_code))
    ).scalar_one_or_none()
    if device is None:
        return DevicePollOut(status=DEVICE_EXPIRED)

    device = _expire_if_needed(db, device)

    if device.status != DEVICE_APPROVED:
        return DevicePollOut(status=device.status)

    token = device.token_plaintext
    profile = None
    if device.user_id is not None:
        user = db.get(User, device.user_id)
        if user is not None:
            profile = profile_out(db, user)

    if token is not None:
        # Single-use reveal: clear the plaintext the moment it leaves.
        device.token_plaintext = None
        db.commit()
        log.info("device_token_delivered", device_id=str(device.id))

    return DevicePollOut(status=DEVICE_APPROVED, token=token, profile=profile)


@router.get("/pending", response_model=DevicePendingOut)
def device_pending(
    code: str = Query(..., min_length=1, max_length=32),
    _: User = Depends(require_sso_user),
    db: Session = Depends(get_db),
) -> DevicePendingOut:
    """What the ``/connect`` page shows before the user decides.

    SSO-gated: the device name is the only thing standing between the user
    and approving somebody else's request, so it isn't public.
    """
    device = _load_by_user_code(db, code)
    return DevicePendingOut(
        user_code=device.user_code,
        device_name=device.device_name,
        status=device.status,
        requested_at=device.created_at,
        expires_at=device.expires_at,
    )


@router.post("/approve", response_model=DeviceDecisionOut)
def device_approve(
    payload: DeviceCodeIn,
    current: User = Depends(require_sso_user),
    db: Session = Depends(get_db),
) -> DeviceDecisionOut:
    """Bind the request to the signed-in profile and mint its token."""
    device = _load_by_user_code(db, payload.user_code)
    if device.status != DEVICE_PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"That request is already {device.status}.",
        )

    plaintext, prefix, token_hash = new_token()
    token = ApiToken(
        user_id=current.id,
        name=device.device_name,
        prefix=prefix,
        token_hash=token_hash,
    )
    db.add(token)
    db.flush()

    device.status = DEVICE_APPROVED
    device.user_id = current.id
    device.token_id = token.id
    device.token_plaintext = plaintext
    device.approved_at = _now()
    db.commit()

    log.info("device_approved", device_id=str(device.id), handle=current.handle)
    return DeviceDecisionOut(status=DEVICE_APPROVED, device_name=device.device_name)


@router.post("/deny", response_model=DeviceDecisionOut)
def device_deny(
    payload: DeviceCodeIn,
    current: User = Depends(require_sso_user),
    db: Session = Depends(get_db),
) -> DeviceDecisionOut:
    """Refuse the request. Terminal — the CLI must start a fresh one."""
    device = _load_by_user_code(db, payload.user_code)
    if device.status != DEVICE_PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"That request is already {device.status}.",
        )
    device.status = DEVICE_DENIED
    device.token_plaintext = None
    db.commit()
    log.info("device_denied", device_id=str(device.id), handle=current.handle)
    return DeviceDecisionOut(status=DEVICE_DENIED, device_name=device.device_name)
