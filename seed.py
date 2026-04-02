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


SAMPLE_AIRCRAFT = [
    {"tail": "N12345", "type": "G650/650ER",    "operator": "Transient", "lat": 25.8067, "lng": -80.2902, "adg": 3},
    {"tail": "N98765", "type": "Citation X+",    "operator": "Charter",   "lat": 25.8063, "lng": -80.2898, "adg": 2},
    {"tail": "N55ABC", "type": "Falcon 2000LXS", "operator": "Private",   "lat": 25.8059, "lng": -80.2895, "adg": 2},
    {"tail": "N777TG", "type": "BBJ 737-700",    "operator": "VIP",       "lat": 25.8068, "lng": -80.2922, "adg": 4},
    {"tail": "N321XJ", "type": "Global 6000",    "operator": "Private",   "lat": 25.8064, "lng": -80.2926, "adg": 3},
    {"tail": "N444MR", "type": "Phenom 300E",    "operator": "Transient", "lat": 25.8053, "lng": -80.2914, "adg": 2},
    {"tail": "N876DF", "type": "PC-12",          "operator": "Charter",   "lat": 25.8051, "lng": -80.2990, "adg": 2},
    {"tail": "N100LJ", "type": "Learjet 45",     "operator": "Transient", "lat": 25.8046, "lng": -80.2987, "adg": 1},
    {"tail": "N200GX", "type": "G550",           "operator": "Private",   "lat": 25.8059, "lng": -80.2835, "adg": 3},
    {"tail": "N350KA", "type": "King Air 350",   "operator": "Charter",   "lat": 25.8045, "lng": -80.2675, "adg": 2},
]


async def seed_aircraft(db: AsyncSession) -> None:
    result = await db.execute(text("SELECT COUNT(*) FROM aircraft"))
    count = result.scalar()
    if count and count > 0:
        return

    inserted = 0
    for ac in SAMPLE_AIRCRAFT:
        # Look up dimensions from aircraft_types
        type_result = await db.execute(
            text("SELECT wingspan_m, length_m FROM aircraft_types WHERE model = :m LIMIT 1"),
            {"m": ac["type"]},
        )
        type_row = type_result.mappings().first()
        if type_row:
            ws = type_row["wingspan_m"]
            ln = type_row["length_m"] or ws * 0.85
        else:
            midpoints = {1: 12.0, 2: 19.5, 3: 30.0, 4: 44.0, 5: 58.5, 6: 72.5}
            ws = midpoints.get(ac["adg"], 19.5)
            ln = ws * 0.85

        await db.execute(
            text("""
                INSERT INTO aircraft (id, tail_number, type, operator, lat, lng, adg_class, heading, wingspan_m, length_m)
                VALUES (:id, :tail, :type, :operator, :lat, :lng, :adg, 0, :ws, :ln)
            """),
            {
                "id": str(uuid7()),
                "tail": ac["tail"],
                "type": ac["type"],
                "operator": ac["operator"],
                "lat": ac["lat"],
                "lng": ac["lng"],
                "adg": ac["adg"],
                "ws": ws,
                "ln": ln,
            },
        )
        inserted += 1

    await db.commit()
    print(f"[seed] Inserted {inserted} sample aircraft")


# ── CLI entry point ──────────────────────────────────────────────────
# Run manually: python seed.py
# Seeds ramps + aircraft into the DB for testing.

if __name__ == "__main__":
    import asyncio
    from database import AsyncSessionLocal

    async def run():
        async with AsyncSessionLocal() as db:
            await seed_ramps(db)
            await seed_aircraft(db)
        print("[seed] Done.")

    asyncio.run(run())
