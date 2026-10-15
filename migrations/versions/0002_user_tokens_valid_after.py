"""users.tokens_valid_after - sign-out stamp for API tokens (v1.2)

API tokens issued before this moment are refused. It is set when an account is deactivated, its role changes,
or it is recovered with `create-admin`, so an old token cannot come back to life when the account is
re-activated (QA-03 follow-up).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("tokens_valid_after", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("tokens_valid_after")
