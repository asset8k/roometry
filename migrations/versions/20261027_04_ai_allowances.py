"""Durable daily AI sessions and per-room actions.

Revision ID: 20261027_04
Revises: 20261026_03
"""

import sqlalchemy as sa
from alembic import op

revision = "20261027_04"
down_revision = "20261026_03"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "quota_buckets",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
    )
    op.create_table(
        "quota_rooms",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "bucket", sa.String(80), sa.ForeignKey("quota_buckets.key"), nullable=False
        ),
        sa.Column("ip_bucket", sa.String(80)),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("actions", sa.Integer(), nullable=False),
        sa.Column("baseline", sa.Integer(), nullable=False),
        sa.Column("first_action", sa.String(36), nullable=False),
    )
    op.create_table(
        "quota_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "room_id",
            sa.String(36),
            sa.ForeignKey("quota_rooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("started", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_quota_actions_room_id", "quota_actions", ["room_id"])


def downgrade():
    op.drop_index("ix_quota_actions_room_id", table_name="quota_actions")
    op.drop_table("quota_actions")
    op.drop_table("quota_rooms")
    op.drop_table("quota_buckets")
