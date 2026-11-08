"""App-issued API tokens (``phk_<40 hex>``) and the device-authorization flow.

Both models exist so the phi CLI can authenticate without the user ever
copy-pasting a secret out of a browser: the CLI starts a device
authorization, the user approves it in an SSO-gated tab, and the CLI
polls the token out exactly once.

Storage rules (``README.md`` → "Identity" / "Device flow"):

- The token itself is NEVER stored. Only ``token_hash`` (sha256 of the
  full ``phk_...`` string) and a short ``prefix`` for display.
- ``DeviceAuth.device_code`` is likewise stored hashed — it is a bearer
  secret the polling CLI presents, so a DB read shouldn't yield one.
  ``user_code`` stays plaintext: the user reads it off their terminal
  and it arrives as a URL query param, so there is nothing to protect
  and an indexed equality lookup is what ``/connect`` needs.
- ``DeviceAuth.token_plaintext`` holds the minted token for the single
  poll that collects it, then is nulled. That is the one moment a
  secret sits at rest, bounded by the 15-minute expiry.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db import Base
from app.models.user import User

# Device-flow status values. Kept as a plain string column rather than a
# PG enum so adding a state later is a no-op migration.
DEVICE_PENDING = "pending"
DEVICE_APPROVED = "approved"
DEVICE_DENIED = "denied"
DEVICE_EXPIRED = "expired"


class ApiToken(Base):
    """A ``phk_`` bearer token, stored hashed."""

    __tablename__ = "api_tokens"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Human label ("sample-laptop", "CI"), shown in the token list.
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # First few characters of the token ("phk_1a2b3c4d") so a user can tell
    # two tokens apart in the UI without the hub ever holding the secret.
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    # sha256 hex of the full token string. Unique so a (astronomically
    # unlikely) generator collision surfaces as an IntegrityError rather
    # than two users sharing a credential.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(User, lazy="joined")

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class DeviceAuth(Base):
    """One in-flight (or settled) device-authorization request."""

    __tablename__ = "device_auths"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # sha256 hex of the device_code handed to the CLI.
    device_code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # 8-char A–Z2–7 (Crockford-ish base32, no 0/1/8/9 or I/L/O) code the
    # human reads. Unique among ALL rows — expired rows keep their code, so
    # a fresh start retries generation on the rare collision.
    user_code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
    device_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=DEVICE_PENDING)
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    token_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("api_tokens.id", ondelete="SET NULL")
    )
    # The minted token, held ONLY until the CLI's next poll collects it.
    token_plaintext: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User | None] = relationship(User, lazy="joined")
