"""registered_run_recovery

Revision ID: 20261026_03
Revises: 20261026_02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261026_03"
down_revision = "20261026_02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "runtime_inputs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column(
            "prepared",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column(
            "images",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column(
            "vision",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_runtime_inputs_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_runtime_inputs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "id", name=op.f("pk_runtime_inputs")),
    )
    op.create_index(
        op.f("ix_runtime_inputs_project_id"),
        "runtime_inputs",
        ["project_id"],
        unique=False,
    )
    op.create_table(
        "runtime_runs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("input_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=True),
        sa.Column(
            "data",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column(
            "usage",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("execution_owner", sa.String(length=36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_runtime_runs_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "input_id"],
            ["runtime_inputs.user_id", "runtime_inputs.id"],
            name=op.f("fk_runtime_runs_user_id_runtime_inputs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_runtime_runs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "id", name=op.f("pk_runtime_runs")),
        sa.UniqueConstraint(
            "user_id", "kind", "request_id", name=op.f("uq_runtime_runs_user_id")
        ),
    )
    op.create_index(
        op.f("ix_runtime_runs_project_id"), "runtime_runs", ["project_id"], unique=False
    )
    op.create_table(
        "graph_checkpoints",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("namespace", sa.String(length=512), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=80), nullable=False),
        sa.Column("parent_id", sa.String(length=80), nullable=True),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column(
            "metadata_json",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "run_id"],
            ["runtime_runs.user_id", "runtime_runs.id"],
            name=op.f("fk_graph_checkpoints_user_id_runtime_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            "run_id",
            "namespace",
            "checkpoint_id",
            name=op.f("pk_graph_checkpoints"),
        ),
    )
    op.create_table(
        "graph_writes",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("namespace", sa.String(length=512), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=80), nullable=False),
        sa.Column("task_id", sa.String(length=80), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("task_path", sa.String(length=512), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id", "run_id"],
            ["runtime_runs.user_id", "runtime_runs.id"],
            name=op.f("fk_graph_writes_user_id_runtime_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            "run_id",
            "namespace",
            "checkpoint_id",
            "task_id",
            "idx",
            name=op.f("pk_graph_writes"),
        ),
    )


def downgrade():
    op.drop_table("graph_writes")
    op.drop_table("graph_checkpoints")
    op.drop_index(op.f("ix_runtime_runs_project_id"), table_name="runtime_runs")
    op.drop_table("runtime_runs")
    op.drop_index(op.f("ix_runtime_inputs_project_id"), table_name="runtime_inputs")
    op.drop_table("runtime_inputs")
