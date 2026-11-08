"""Marketplace: org profiles and their publishers

Chains on ``0002_hub_schema``. Two schema changes, both additive:

- ``users.is_org`` — marks a profile as an ORG rather than a person. Orgs
  live in ``users`` so they can own bundles with zero special-casing in the
  bundle code; see ``models/org.py``.
- ``org_publishers`` — who may write on an org's behalf.

**No data is seeded here.** The ``example`` org profile and its initial
publishers (``ORG_INITIAL_PUBLISHERS``) are created by an idempotent
startup step, ``app.orgs.ensure_seed``, invoked from ``main._lifespan``.
Two reasons the seed does not live in this migration:

1. Deriving a handle from an email means either duplicating
   ``identity.handle_from_email`` in SQL (0002 already had to do that once
   for the backfill, and it is exactly the kind of duplication that drifts)
   or importing app code into a migration, which pins the migration to a
   version of that code forever.
2. Migrations run once. The publisher list is *configuration*
   (``ORG_INITIAL_PUBLISHERS``); changing it and redeploying should take
   effect, and a migration that has already run cannot do that.

The startup seed is safe to run on every boot and on every worker — see
its docstring.

Revision ID: 0003_marketplace
Revises: 0002_hub_schema
Create Date: 2026-08-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_marketplace"
down_revision: str | Sequence[str] | None = "0002_hub_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_org", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.create_table(
        "org_publishers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # The org profile being published under.
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The person granted publish rights.
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SET NULL, not CASCADE: losing the granter must not revoke the grant.
        sa.Column(
            "added_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Makes the startup seed a plain idempotent upsert.
        sa.UniqueConstraint("org_id", "user_id", name="uq_org_publishers_org_user"),
    )
    # Reverse lookup ("which orgs may this caller write for?") — the question
    # every owner-scoped bundle write asks.
    op.create_index("ix_org_publishers_user_id", "org_publishers", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_org_publishers_user_id", table_name="org_publishers")
    op.drop_table("org_publishers")
    op.drop_column("users", "is_org")
