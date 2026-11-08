"""phi hub schema: profiles get handles, plus tokens, devices and bundles

Chains on the template's ``0001_initial_users``. Everything the hub needs
beyond the baseline users table lands in one revision:

- ``users.handle`` / ``users.display_name`` — the public identifier and
  the user-editable name (see ``models/user.py`` for why they are
  separate columns).
- ``api_tokens`` / ``device_auths`` — the ``phk_`` credential and the
  device-authorization flow.
- ``bundles`` / ``bundle_versions`` / ``bundle_files`` / ``import_events``
  — the registry itself.

``users.handle`` is added nullable, backfilled from the email local-part,
then made NOT NULL. On a fresh deploy the backfill is a no-op; doing it
this way means the same migration also works against an environment that
already collected sign-ins under the template's schema.

Revision ID: 0002_hub_schema
Revises: 0001_initial_users
Create Date: 2026-08-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_hub_schema"
down_revision: str | Sequence[str] | None = "0001_initial_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------------- users
    op.add_column("users", sa.Column("handle", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("display_name", sa.String(200), nullable=True))

    # Backfill: email local-part, non-[a-z0-9-] squashed to dashes, with the
    # row number appended on collision. `regexp_replace` with the 'g' flag
    # mirrors `identity.handle_from_email`; the numbered fallback mirrors
    # `identity.unique_handle`.
    op.execute(
        """
        WITH base AS (
            SELECT
                id,
                NULLIF(
                    TRIM(BOTH '-' FROM regexp_replace(
                        lower(split_part(email, '@', 1)), '[^a-z0-9-]+', '-', 'g'
                    )),
                    ''
                ) AS candidate
            FROM users
        ), numbered AS (
            SELECT
                id,
                COALESCE(candidate, 'user') AS candidate,
                ROW_NUMBER() OVER (
                    PARTITION BY COALESCE(candidate, 'user') ORDER BY id
                ) AS rn
            FROM base
        )
        UPDATE users u
        SET handle = CASE
            WHEN n.rn = 1 THEN n.candidate
            ELSE n.candidate || n.rn::text
        END
        FROM numbered n
        WHERE u.id = n.id
        """
    )
    op.alter_column("users", "handle", nullable=False)
    op.create_unique_constraint("uq_users_handle", "users", ["handle"])

    # ----------------------------------------------------------- api_tokens
    op.create_table(
        "api_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        # sha256 hex of the full `phk_...` string — the token itself is
        # never stored.
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_api_tokens_user_id", "api_tokens", ["user_id"])

    # --------------------------------------------------------- device_auths
    op.create_table(
        "device_auths",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("device_code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_code", sa.String(8), nullable=False, unique=True),
        sa.Column("device_name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "token_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("api_tokens.id", ondelete="SET NULL"),
        ),
        # Held only until the CLI's next poll collects it, then nulled.
        sa.Column("token_plaintext", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
    )

    # -------------------------------------------------------------- bundles
    op.create_table(
        "bundles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(20), nullable=False, server_default="mixed"),
        sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("latest_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("owner_id", "slug", name="uq_bundles_owner_slug"),
    )
    op.create_index("ix_bundles_kind", "bundles", ["kind"])
    op.create_index("ix_bundles_updated_at", "bundles", ["updated_at"])

    # ------------------------------------------------------ bundle_versions
    op.create_table(
        "bundle_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "bundle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bundles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changelog", sa.Text(), nullable=False, server_default=""),
        sa.Column("pi_manifest", sa.JSON()),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.UniqueConstraint("bundle_id", "version", name="uq_bundle_versions_bundle_version"),
    )

    # --------------------------------------------------------- bundle_files
    op.create_table(
        "bundle_files",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bundle_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(400), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("is_text", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("version_id", "path", name="uq_bundle_files_version_path"),
    )

    # -------------------------------------------------------- import_events
    op.create_table(
        "import_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "bundle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bundles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # The install-count window function partitions by (bundle_id, user_id)
    # and orders by created_at — this index is exactly that access path.
    op.create_index(
        "ix_import_events_bundle_user_time",
        "import_events",
        ["bundle_id", "user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_import_events_bundle_user_time", table_name="import_events")
    op.drop_table("import_events")
    op.drop_table("bundle_files")
    op.drop_table("bundle_versions")
    op.drop_index("ix_bundles_updated_at", table_name="bundles")
    op.drop_index("ix_bundles_kind", table_name="bundles")
    op.drop_table("bundles")
    op.drop_table("device_auths")
    op.drop_index("ix_api_tokens_user_id", table_name="api_tokens")
    op.drop_table("api_tokens")
    op.drop_constraint("uq_users_handle", "users", type_="unique")
    op.drop_column("users", "display_name")
    op.drop_column("users", "handle")
