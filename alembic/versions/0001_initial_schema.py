"""Initial schema: geofences, parking_spots, aircraft + PostGIS + triggers

Revision ID: 0001
Revises:
Create Date: 2026-03-19
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import geoalchemy2

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "geofences",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), primary_key=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("tenant", sa.Text()),
        sa.Column("tenant_id", sa.Text()),
        sa.Column("is_fbo", sa.Boolean()),
        sa.Column("color", sa.Text(), nullable=False, server_default="#3b82f6"),
        sa.Column(
            "geometry",
            geoalchemy2.types.Geometry("POLYGON", srid=4326),
            nullable=False,
        ),
        sa.Column("area_sqft", sa.Float()),
        sa.Column("capacity", sa.Integer()),
        sa.Column("is_terminal", sa.Boolean()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("created_by", sa.Text()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_by", sa.Text()),
    )
    op.create_index("ix_geofences_geometry", "geofences", ["geometry"], postgresql_using="gist")

    op.create_table(
        "parking_spots",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.types.Geometry("POLYGON", srid=4326),
            nullable=False,
        ),
        sa.Column(
            "accepted_sizes",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY['S','M','L','XL']"),
        ),
        sa.Column("ramp_zone_id", sa.UUID(), sa.ForeignKey("geofences.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("created_by", sa.Text()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_by", sa.Text()),
    )
    op.create_index(
        "ix_parking_spots_geometry", "parking_spots", ["geometry"], postgresql_using="gist"
    )

    op.create_table(
        "aircraft",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), primary_key=True),
        sa.Column("tail_number", sa.Text(), nullable=False, unique=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("operator", sa.Text(), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("size", sa.String(2), nullable=False),
        sa.Column("spot_id", sa.UUID(), sa.ForeignKey("parking_spots.id", ondelete="SET NULL")),
        sa.Column("highlighted", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("created_by", sa.Text()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_by", sa.Text()),
        sa.CheckConstraint("size IN ('S','M','L','XL')", name="ck_aircraft_size"),
    )

    # updated_at trigger function
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for tbl in ("geofences", "parking_spots", "aircraft"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{tbl}_updated_at
            BEFORE UPDATE ON {tbl}
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
            """
        )


def downgrade() -> None:
    for tbl in ("aircraft", "parking_spots", "geofences"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{tbl}_updated_at ON {tbl}")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at")
    op.drop_table("aircraft")
    op.drop_table("parking_spots")
    op.drop_table("geofences")
