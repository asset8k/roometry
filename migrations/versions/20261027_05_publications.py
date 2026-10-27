"""Explicit public room snapshots.

Revision ID: 20261027_05
Revises: 20261027_04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261027_05"
down_revision = "20261027_04"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "publications",
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("public_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column(
            "version_id",
            sa.Uuid(),
            sa.ForeignKey("scene_versions.id", ondelete="CASCADE"),
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("summary", sa.String(240), nullable=False),
        sa.Column("scene", sa.JSON().with_variant(postgresql.JSONB(), "postgresql")),
        sa.Column("preview", sa.LargeBinary()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_publications_feed", "publications", ["active", "published_at", "public_id"]
    )
    op.create_index("ix_publications_version_id", "publications", ["version_id"])


def downgrade():
    op.drop_index("ix_publications_version_id", table_name="publications")
    op.drop_index("ix_publications_feed", table_name="publications")
    op.drop_table("publications")
