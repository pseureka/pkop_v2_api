"""Split geofences into ramps and zones tables with hierarchy

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-27
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import geoalchemy2

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Create ramps table ───────────────────────────────────────────────
    op.create_table(
        "ramps",
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
    op.create_index("ix_ramps_geometry", "ramps", ["geometry"], postgresql_using="gist")

    # ── Create zones table ───────────────────────────────────────────────
    op.create_table(
        "zones",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), primary_key=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("color", sa.Text(), nullable=False, server_default="#22c55e"),
        sa.Column(
            "geometry",
            geoalchemy2.types.Geometry("POLYGON", srid=4326),
            nullable=False,
        ),
        sa.Column("ramp_id", sa.UUID(), sa.ForeignKey("ramps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("area_sqft", sa.Float()),
        sa.Column("capacity", sa.Integer()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("created_by", sa.Text()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_by", sa.Text()),
    )
    op.create_index("ix_zones_geometry", "zones", ["geometry"], postgresql_using="gist")
    op.create_index("ix_zones_ramp_id", "zones", ["ramp_id"])

    # ── Migrate geofences data into ramps ────────────────────────────────
    op.execute(
        """
        INSERT INTO ramps (id, label, tenant, tenant_id, is_fbo, color, geometry, area_sqft, capacity, is_terminal, created_at, created_by, updated_at, updated_by)
        SELECT id, label, tenant, tenant_id, is_fbo, color, geometry, area_sqft, capacity, is_terminal, created_at, created_by, updated_at, updated_by
        FROM geofences
        """
    )

    # ── Alter parking_spots: replace ramp_zone_id with zone_id ───────────
    # Drop old FK and column
    op.drop_constraint("parking_spots_ramp_zone_id_fkey", "parking_spots", type_="foreignkey")
    op.drop_column("parking_spots", "ramp_zone_id")
    # Add new zone_id column (nullable for now — existing spots have no zone yet)
    op.add_column(
        "parking_spots",
        sa.Column("zone_id", sa.UUID(), sa.ForeignKey("zones.id", ondelete="CASCADE"), nullable=True),
    )
    op.create_index("ix_parking_spots_zone_id", "parking_spots", ["zone_id"])

    # ── Drop geofences table ─────────────────────────────────────────────
    op.execute("DROP TRIGGER IF EXISTS trg_geofences_updated_at ON geofences")
    op.drop_index("ix_geofences_geometry", table_name="geofences")
    op.drop_table("geofences")

    # ── Add updated_at triggers for new tables ───────────────────────────
    for tbl in ("ramps", "zones"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{tbl}_updated_at
            BEFORE UPDATE ON {tbl}
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
            """
        )


def downgrade() -> None:
    # Drop triggers
    for tbl in ("zones", "ramps"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{tbl}_updated_at ON {tbl}")

    # Recreate geofences table
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
    op.execute(
        """
        CREATE TRIGGER trg_geofences_updated_at
        BEFORE UPDATE ON geofences
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )

    # Migrate ramps data back to geofences
    op.execute(
        """
        INSERT INTO geofences (id, label, tenant, tenant_id, is_fbo, color, geometry, area_sqft, capacity, is_terminal, created_at, created_by, updated_at, updated_by)
        SELECT id, label, tenant, tenant_id, is_fbo, color, geometry, area_sqft, capacity, is_terminal, created_at, created_by, updated_at, updated_by
        FROM ramps
        """
    )

    # Restore parking_spots FK
    op.drop_index("ix_parking_spots_zone_id", table_name="parking_spots")
    op.drop_column("parking_spots", "zone_id")
    op.add_column(
        "parking_spots",
        sa.Column("ramp_zone_id", sa.UUID(), sa.ForeignKey("geofences.id", ondelete="SET NULL")),
    )

    # Drop new tables
    op.drop_index("ix_zones_ramp_id", table_name="zones")
    op.drop_index("ix_zones_geometry", table_name="zones")
    op.drop_table("zones")
    op.drop_index("ix_ramps_geometry", table_name="ramps")
    op.drop_table("ramps")
