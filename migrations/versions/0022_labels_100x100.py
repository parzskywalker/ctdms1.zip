"""v1.9 - case label stickers are 100 x 100 mm (owner, 26 Nov): the label height setting moves from the old 50 mm
default to 100 mm. A height someone set by hand (anything but 50) is left alone.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-26
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE settings SET value = '100' WHERE key = 'labels.height_mm' AND value = '50'")


def downgrade() -> None:
    op.execute("UPDATE settings SET value = '50' WHERE key = 'labels.height_mm' AND value = '100'")
