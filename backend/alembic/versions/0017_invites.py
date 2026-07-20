"""Invite codes — gated registration, each binding a new user to a clinic (#51)

No one self-registers without one. An admin mints an invite for a clinic; redeeming it is what
sets the clinic_id a new user is given, and a registrant can never choose that for themselves
(core/auth.py refuses a client-supplied clinic_id). Only the code's sha256 is stored, so a
database leak exposes no usable code — see app/models/invite.py on why no salt is needed here.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("clinic_id", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_by", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # Unique so the same code cannot be minted twice, and the lookup key on redemption.
        sa.UniqueConstraint("code_hash", name="uq_invites_code_hash"),
    )
    op.create_index("ix_invites_code_hash", "invites", ["code_hash"])
    op.create_index("ix_invites_clinic_id", "invites", ["clinic_id"])
    op.create_index("ix_invites_created_by", "invites", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_invites_created_by", table_name="invites")
    op.drop_index("ix_invites_clinic_id", table_name="invites")
    op.drop_index("ix_invites_code_hash", table_name="invites")
    op.drop_table("invites")
