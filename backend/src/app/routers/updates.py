"""Batch update check (contract → "Marketplace (v2)" → update checking).

One endpoint, one job: a phi client hands over everything it has installed
and gets back what has moved. It is a POST rather than a GET because the
request body is a list whose length is the client's whole hub — a query
string would be the wrong shape and, past a few dozen bundles, too long.

The client calls this at most every 6 hours (throttled, async, never
blocking session start), so the cost that matters is per-call, not
per-second: :func:`app.queries.check_updates` resolves the entire batch in
a single statement.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import queries
from app.db import get_db
from app.deps import get_current_user
from app.models.user import User
from app.schemas.org import UpdateCheckIn, UpdateCheckOut

router = APIRouter(prefix="/updates", tags=["updates"])


@router.post("/check", response_model=UpdateCheckOut)
def check_updates(
    payload: UpdateCheckIn,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UpdateCheckOut:
    """Which installed bundles have a newer published version.

    Send ``{"installed": [{"ref": "example/routing", "version": 3}, ...]}``.
    Bundles that are current appear in neither list; refs that no longer
    resolve (soft-deleted, or never existed) come back under ``missing`` so
    the client can prune its state instead of retrying them forever.

    An empty ``installed`` is valid and returns two empty lists — a fresh
    hub asking the question is not an error.
    """
    return queries.check_updates(db, [(entry.ref, entry.version) for entry in payload.installed])
