"""Add type_id FK to aircraft, drop type/wingspan_m/length_m columns, delete orphan aircraft

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-30
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add type_id column (nullable initially)
    op.add_column("aircraft", sa.Column("type_id", sa.UUID(), nullable=True))

    # 2. Backfill type_id from aircraft.type → aircraft_types (match on make+model or model)
    op.execute("""
        UPDATE aircraft a
        SET type_id = sub.type_id
        FROM (
            SELECT DISTINCT ON (a2.id) a2.id AS aircraft_id, t.id AS type_id
            FROM aircraft a2
            JOIN aircraft_types t
              ON t.make || ' ' || t.model = a2.type
              OR t.model = a2.type
            ORDER BY a2.id, t.id
        ) sub
        WHERE a.id = sub.aircraft_id
    """)

    # 3. Delete aircraft that couldn't be matched (type doesn't exist in aircraft_types)
    op.execute("DELETE FROM aircraft WHERE type_id IS NULL")

    # 4. Make type_id NOT NULL and add FK constraint
    op.alter_column("aircraft", "type_id", nullable=False)
    op.create_foreign_key(
        "fk_aircraft_type_id",
        "aircraft", "aircraft_types",
        ["type_id"], ["id"],
    )
    op.create_index("ix_aircraft_type_id", "aircraft", ["type_id"])

    # 5. Drop redundant columns
    op.drop_column("aircraft", "type")
    op.drop_column("aircraft", "wingspan_m")
    op.drop_column("aircraft", "length_m")


def downgrade() -> None:
    # Restore columns
    op.add_column("aircraft", sa.Column("type", sa.Text(), nullable=True))
    op.add_column("aircraft", sa.Column("wingspan_m", sa.Float(), nullable=True))
    op.add_column("aircraft", sa.Column("length_m", sa.Float(), nullable=True))

    # Backfill type from aircraft_types
    op.execute("""
        UPDATE aircraft a
        SET type = t.make || ' ' || t.model,
            wingspan_m = t.wingspan_m,
            length_m = t.length_m
        FROM aircraft_types t
        WHERE a.type_id = t.id
    """)

    op.alter_column("aircraft", "type", nullable=False)

    # Drop FK and column
    op.drop_index("ix_aircraft_type_id", "aircraft")
    op.drop_constraint("fk_aircraft_type_id", "aircraft", type_="foreignkey")
    op.drop_column("aircraft", "type_id")
