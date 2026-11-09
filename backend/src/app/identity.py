"""Identity primitives: handles, ``phk_`` tokens, device user-codes.

Pure functions + the two DB helpers that own uniqueness. Kept out of
``deps.py`` so the MCP surface (which resolves identity from raw headers
rather than a FastAPI dependency) can reuse exactly the same logic — one
implementation of "who is this and do they have a profile", two callers.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime

from forwarded_auth import InvalidIdentityToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.token import ApiToken
from app.models.user import User

# ``phk_`` + 40 hex chars (contract → "Identity"). 20 random bytes.
TOKEN_PREFIX = "phk_"
_TOKEN_BYTES = 20

# The visible slice stored alongside the hash so the UI can tell two
# tokens apart: the marker plus the first 8 hex chars.
_PREFIX_DISPLAY_LEN = len(TOKEN_PREFIX) + 8

# Device user-code alphabet: A–Z plus 2–7 (RFC 4648 base32). Excludes
# 0/1/8/9 so there is no 0-vs-O or 1-vs-I ambiguity when a human reads
# the code off a terminal and types it into a browser.
_USER_CODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
USER_CODE_LENGTH = 8

# Handles are lowercase, dash-separated, 2–64 chars — the same shape as
# bundle slugs so `owner/slug` is uniformly URL-safe.
_HANDLE_STRIP = re.compile(r"[^a-z0-9-]+")
_HANDLE_COLLAPSE = re.compile(r"-{2,}")
_MAX_HANDLE_LEN = 60  # leaves room for a numeric collision suffix


def new_token() -> tuple[str, str, str]:
    """Mint a token. Returns ``(plaintext, prefix, sha256_hex)``.

    The plaintext is returned to the caller exactly once and never
    persisted (except transiently on ``DeviceAuth.token_plaintext``, for
    the single poll that collects it).
    """
    plaintext = TOKEN_PREFIX + secrets.token_hex(_TOKEN_BYTES)
    return plaintext, plaintext[:_PREFIX_DISPLAY_LEN], hash_token(plaintext)


def hash_token(plaintext: str) -> str:
    """sha256 hex of a token string — the only form the hub stores."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def looks_like_app_token(value: str) -> bool:
    """True for strings the dual-channel auth should route to the DB.

    Deliberately a prefix check and not a full-shape check: a malformed
    ``phk_`` value must 401 as a *bad app token*, not silently fall
    through to the identity-provider branch where it would 401 with a confusing
    "invalid identity token" instead.
    """
    return value.startswith(TOKEN_PREFIX)


def new_user_code() -> str:
    """A fresh 8-char A–Z2–7 device code."""
    return "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(USER_CODE_LENGTH))


def normalize_user_code(value: str) -> str:
    """Canonicalize what a human typed: upper-cased, whitespace and
    dashes stripped. ``phi-lab top`` and ``PHILABTOP`` hit the same row."""
    return re.sub(r"[\s-]+", "", value or "").upper()


def handle_from_email(email: str) -> str:
    """Derive a base handle from an email local-part.

    ``publisher@example.com`` → ``publisher``; ``first.last@…`` → ``first-last``.
    Not guaranteed unique — see :func:`unique_handle`.
    """
    local = (email or "").split("@", 1)[0].lower()
    candidate = _HANDLE_COLLAPSE.sub("-", _HANDLE_STRIP.sub("-", local)).strip("-")
    candidate = candidate[:_MAX_HANDLE_LEN].strip("-")
    # A local-part of pure punctuation (or an empty email) would leave
    # nothing; `user` keeps the NOT NULL column satisfiable, and
    # `unique_handle` will suffix it.
    return candidate or "user"


def unique_handle(db: Session, base: str) -> str:
    """``base``, or ``base2``/``base3``/… if taken.

    The contract says "on collision append a digit". We start at 2 (the
    first colliding user is conceptually the second holder of the name)
    and keep counting; the loop is bounded only by how many people share
    an email local-part, which in a single identity domain is zero.
    """
    if db.execute(select(User.id).where(User.handle == base)).first() is None:
        return base
    suffix = 2
    while True:
        candidate = f"{base[: _MAX_HANDLE_LEN - len(str(suffix))]}{suffix}"
        if db.execute(select(User.id).where(User.handle == candidate)).first() is None:
            return candidate
        suffix += 1


def find_or_create_stub_profile(
    db: Session,
    *,
    email: str,
    name: str | None = None,
    is_org: bool = False,
) -> User:
    """Find-or-create a profile for someone who has NOT signed in yet.

    Same handle derivation and collision suffixing as a real sign-in — that
    is the whole point of it living here — but deliberately different in
    two ways:

    - ``last_sign_in_at`` stays NULL. The column reads as "last active", and
      a profile conjured by the marketplace seed or by
      ``POST /api/orgs/{handle}/publishers`` has never been active. Once the
      person does sign in, :func:`find_or_create_profile` finds this row and
      starts stamping it.
    - ``is_admin`` is never granted. The bootstrap-admin rule below is about
      "the first human through the door"; a stub is not that, and a seeded
      org profile least of all.

    Does NOT commit — the caller owns the transaction, so a seed that grants
    three publishers is one atomic unit rather than three.
    """
    email = (email or "").lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is not None:
        return user
    user = User(
        email=email,
        handle=unique_handle(db, handle_from_email(email)),
        name=name,
        is_org=is_org,
    )
    db.add(user)
    db.flush()
    return user


def find_or_create_profile(
    db: Session,
    *,
    email: str,
    name: str | None,
    avatar_url: str | None = None,
) -> User:
    """Find-or-create the profile for an authenticated identity.

    Called on EVERY authenticated touch through any channel — there is no
    signup step in the hub (contract → "Profiles are auto-created on first
    authenticated touch"). Refreshes the IdP-provided ``name`` / avatar
    when they change, bumps ``last_sign_in_at`` (read it as "last active"),
    and preserves the template's bootstrap-admin rule.

    ``display_name`` is intentionally NOT touched here: it is the user's
    own editable value and the IdP has no say in it.
    """
    email = (email or "").lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is not None and user.is_org:
        raise InvalidIdentityToken("Organization profiles cannot sign in")
    # "First HUMAN through the door", evaluated before this sign-in is
    # stamped. Both halves of that phrase matter now that the marketplace
    # seed pre-creates rows: an org profile is not a person, and a publisher
    # stub that has never signed in has not walked through any door. Without
    # this the bootstrap admin would land on a seeded row (which can never
    # sign in to use it) and the first real user would silently get nothing.
    is_first_human = (
        db.execute(
            select(User.id).where(User.is_org.is_(False), User.last_sign_in_at.isnot(None))
        ).first()
        is None
    )
    if user is None:
        user = User(
            email=email,
            handle=unique_handle(db, handle_from_email(email)),
            name=name,
            avatar_url=avatar_url,
            is_admin=is_first_human,
        )
        db.add(user)
        db.flush()
    else:
        if name and user.name != name:
            user.name = name
        if avatar_url and user.avatar_url != avatar_url:
            user.avatar_url = avatar_url
        # Adopting a seeded stub: this IS their first sign-in, so the
        # bootstrap grant is still theirs to take.
        if is_first_human and user.last_sign_in_at is None and not user.is_org:
            user.is_admin = True
    user.last_sign_in_at = datetime.now(UTC)
    db.commit()
    db.refresh(user)
    return user


def user_for_app_token(db: Session, plaintext: str) -> User | None:
    """Resolve a ``phk_`` token to its owner, or ``None``.

    Returns ``None`` for unknown *and* revoked tokens — a revoked token
    must be indistinguishable from a bogus one to the caller. Bumps
    ``last_used_at`` so the token list can show staleness.
    """
    token = db.execute(
        select(ApiToken).where(ApiToken.token_hash == hash_token(plaintext))
    ).scalar_one_or_none()
    if token is None or token.revoked_at is not None:
        return None
    token.last_used_at = datetime.now(UTC)
    db.commit()
    return token.user
