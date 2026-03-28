"""
Seeds the ramps table from provideddata/response_1773852479638.json
if the table is empty. Called on app startup.
"""

import json
from uuid_utils import uuid7
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from utils.geometry import json_polygon_to_wkt

# Relative to this file: provideddata/response_1773852479638.json
DATA_FILE = Path(__file__).parent / "provideddata" / "response_1773852479638.json"

COLOR_PALETTE = [
    "#3b82f6",
    "#22c55e",
    "#ef4444",
    "#f59e0b",
    "#a855f7",
    "#06b6d4",
    "#ec4899",
    "#14b8a6",
]


async def seed_ramps(db: AsyncSession) -> None:
    result = await db.execute(text("SELECT COUNT(*) FROM ramps"))
    count = result.scalar()
    if count and count > 0:
        return  # Already seeded

    if not DATA_FILE.exists():
        print(f"[seed] Data file not found: {DATA_FILE}")
        return

    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)

    tenants = data.get("data", {}).get("tenants", [])
    color_idx = 0
    inserted = 0

    for tenant_obj in tenants:
        tenant_name = tenant_obj.get("tenant")
        tenant_id = tenant_obj.get("tenant_id")
        is_fbo = tenant_obj.get("is_fbo")
        ramps = tenant_obj.get("ramps", [])

        for ramp in ramps:
            polygon = ramp.get("polygon", [])
            if not polygon:
                continue

            wkt = json_polygon_to_wkt(polygon)
            color = COLOR_PALETTE[color_idx % len(COLOR_PALETTE)]
            color_idx += 1

            await db.execute(
                text(
                    """
                    INSERT INTO ramps
                        (id, label, tenant, tenant_id, is_fbo, color, geometry,
                         area_sqft, capacity, is_terminal)
                    VALUES
                        (:id, :label, :tenant, :tenant_id, :is_fbo, :color,
                         ST_GeomFromText(:wkt, 4326),
                         :area_sqft, :capacity, :is_terminal)
                    """
                ),
                {
                    "id": str(uuid7()),
                    "label": ramp.get("label", tenant_name),
                    "tenant": tenant_name,
                    "tenant_id": tenant_id,
                    "is_fbo": is_fbo,
                    "color": color,
                    "wkt": wkt,
                    "area_sqft": ramp.get("area_sqft"),
                    "capacity": ramp.get("capacity"),
                    "is_terminal": ramp.get("is_terminal"),
                },
            )
            inserted += 1

    await db.commit()
    print(f"[seed] Inserted {inserted} ramps from {DATA_FILE.name}")
