"""Remove parking_spots, add heading/zone_id/wingspan_m/length_m to aircraft

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-29
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add new columns to aircraft
    op.add_column("aircraft", sa.Column("heading", sa.Float(), nullable=True))
    op.add_column("aircraft", sa.Column("zone_id", sa.UUID(), nullable=True))
    op.add_column("aircraft", sa.Column("wingspan_m", sa.Float(), nullable=True))
    op.add_column("aircraft", sa.Column("length_m", sa.Float(), nullable=True))

    # Set default heading
    op.execute("UPDATE aircraft SET heading = 0.0 WHERE heading IS NULL")
    op.alter_column("aircraft", "heading", nullable=False, server_default=sa.text("0.0"))

    # 2. Backfill zone_id from spot_id -> parking_spots.zone_id
    op.execute("""
        UPDATE aircraft a
        SET zone_id = ps.zone_id
        FROM parking_spots ps
        WHERE a.spot_id = ps.id AND ps.zone_id IS NOT NULL
    """)

    # Add FK constraint for zone_id
    op.create_foreign_key(
        "fk_aircraft_zone_id", "aircraft", "zones",
        ["zone_id"], ["id"], ondelete="SET NULL"
    )

    # 3. Backfill wingspan_m/length_m from aircraft_types (match on type = designator or model)
    op.execute("""
        UPDATE aircraft a
        SET wingspan_m = at.wingspan_m,
            length_m = COALESCE(at.length_m, at.wingspan_m * 0.85)
        FROM aircraft_types at
        WHERE a.type = at.model OR a.type = at.designator
    """)

    # Fallback: use ADG class midpoint for any remaining NULLs
    op.execute("""
        UPDATE aircraft a
        SET wingspan_m = CASE
                WHEN a.adg_class = 1 THEN 12.0
                WHEN a.adg_class = 2 THEN 19.5
                WHEN a.adg_class = 3 THEN 30.0
                WHEN a.adg_class = 4 THEN 44.0
                WHEN a.adg_class = 5 THEN 58.5
                WHEN a.adg_class = 6 THEN 72.5
                ELSE 19.5
            END
        WHERE a.wingspan_m IS NULL
    """)

    op.execute("""
        UPDATE aircraft SET length_m = wingspan_m * 0.85 WHERE length_m IS NULL
    """)

    # 4. Drop spot_id FK and column
    op.drop_constraint("aircraft_spot_id_fkey", "aircraft", type_="foreignkey")
    op.drop_column("aircraft", "spot_id")

    # 5. Drop parking_spots table
    op.execute("DROP TRIGGER IF EXISTS trg_parking_spots_updated_at ON parking_spots")
    op.drop_table("parking_spots")


def downgrade() -> None:
    # Recreate parking_spots table
    op.create_table(
        "parking_spots",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("geometry", sa.Text(), nullable=False),  # simplified for downgrade
        sa.Column("accepted_classes", sa.ARRAY(sa.Integer()), nullable=False,
                  server_default=sa.text("ARRAY[1,2,3,4,5,6]")),
        sa.Column("zone_id", sa.UUID(), sa.ForeignKey("zones.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("created_by", sa.Text()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_by", sa.Text()),
    )

    # Re-add spot_id to aircraft
    op.add_column("aircraft", sa.Column("spot_id", sa.UUID(), nullable=True))

    # Drop new columns
    op.drop_constraint("fk_aircraft_zone_id", "aircraft", type_="foreignkey")
    op.drop_column("aircraft", "zone_id")
    op.drop_column("aircraft", "heading")
    op.drop_column("aircraft", "wingspan_m")
    op.drop_column("aircraft", "length_m")
