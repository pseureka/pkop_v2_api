"""Add parking_mode to zones (hangar or ramp)

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("zones", sa.Column("parking_mode", sa.Text(), server_default="hangar"))


def downgrade() -> None:
    op.drop_column("zones", "parking_mode")
