"""v1.6 Guided Training - training progress per user (live database only; practice data lives in separate files)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "training_progress",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.String(20), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(12), nullable=False, server_default="NOT_STARTED"),
        sa.Column("current_lesson", sa.String(40)),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lessons", sa.JSON()),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("last_activity_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("assigned_at", sa.DateTime()),
        sa.Column("assigned_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("reset_at", sa.DateTime()),
        sa.Column("reset_by_id", sa.Integer(), sa.ForeignKey("users.id")),
    )
    op.create_index("ix_training_progress_user_id", "training_progress", ["user_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_training_progress_user_id", table_name="training_progress")
    op.drop_table("training_progress")
