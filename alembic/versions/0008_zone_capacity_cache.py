"""Replace parking_mode with capacity_data cache and backfill existing zones

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-17
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
import json
import sys
import os

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _compute_capacity_for_zone(conn, zone_id, zone_wkt):
    """Compute capacity data for a zone and return as dict."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from utils.geometry import wkt_to_frontend_coords
    from utils.optimizer import compute_zone_capacity_units

    zone_coords = wkt_to_frontend_coords(zone_wkt)

    result = conn.execute(text(
        """SELECT adg_class, AVG(wingspan_m) AS avg_ws,
                  AVG(COALESCE(length_m, wingspan_m * 0.85)) AS avg_ln
           FROM aircraft_types GROUP BY adg_class ORDER BY adg_class"""
    ))
    adg_dims = {}
    for r in result.mappings():
        adg_dims[int(r["adg_class"])] = {
            "wingspan_m": float(r["avg_ws"]),
            "length_m": float(r["avg_ln"]),
        }

    if not adg_dims:
        return None

    buffer_ft = 5.0
    capacity_data = {}

    for mode in ["hangar", "ramp"]:
        cap = compute_zone_capacity_units(zone_coords, adg_dims, buffer_ft, mode)
        capacity_data[mode] = {
            "total_units": cap["total_units"],
            "max_by_adg": {str(k): v for k, v in cap["max_by_adg"].items()},
            "adg_weights": {str(k): v for k, v in cap["adg_weights"].items()},
            "buffer_ft": buffer_ft,
        }

    return capacity_data


def upgrade() -> None:
    op.drop_column("zones", "parking_mode")
    op.add_column("zones", sa.Column("capacity_data", sa.JSON))

    conn = op.get_bind()
    zones = conn.execute(text("SELECT id, ST_AsText(geometry) AS geometry FROM zones"))

    for zone in zones.mappings():
        zone_id = str(zone["id"])
        zone_wkt = str(zone["geometry"])
        print(f"  Computing capacity for zone {zone_id}...")

        try:
            data = _compute_capacity_for_zone(conn, zone_id, zone_wkt)
            if data:
                conn.execute(
                    text("UPDATE zones SET capacity_data = :data WHERE id = :id"),
                    {"data": json.dumps(data), "id": zone_id},
                )
                print(f"    Done — hangar: {data['hangar']['total_units']} units, ramp: {data['ramp']['total_units']} units")
        except Exception as e:
            print(f"    Error: {e}")


def downgrade() -> None:
    op.drop_column("zones", "capacity_data")
    op.add_column("zones", sa.Column("parking_mode", sa.Text(), server_default="hangar"))
