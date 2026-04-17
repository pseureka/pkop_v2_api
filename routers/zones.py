import json as json_mod
from uuid import UUID
from uuid_utils import uuid7
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional

from database import get_db
from schemas import ZoneCreate, ZoneUpdate, ZoneRead
from utils.geometry import frontend_coords_to_wkt, wkt_to_frontend_coords
from utils.optimizer import compute_zone_capacity_units

router = APIRouter(prefix="/api/zones", tags=["zones"])

_SELECT_COLS = "id, label, color, ramp_id, area_sqft, capacity, capacity_data, ST_AsText(geometry) AS geometry"


async def _compute_and_store_capacity(
    db: AsyncSession, zone_id: str, zone_coords, buffer_ft: float, parking_mode: str
) -> dict:
    """Compute capacity for a zone at the given mode+buffer and persist to capacity_data,
    merging with any existing cached mode. Returns the final capacity_data dict.

    This is the empty-zone theoretical max — it does NOT account for currently
    parked aircraft. Readers subtract parked weight at query time.
    Does NOT commit — caller controls the transaction.
    """
    adg_result = await db.execute(text(
        """SELECT adg_class, AVG(wingspan_m) AS avg_ws,
                  AVG(COALESCE(length_m, wingspan_m * 0.85)) AS avg_ln
           FROM aircraft_types GROUP BY adg_class ORDER BY adg_class"""
    ))
    adg_dims = {
        int(r["adg_class"]): {"wingspan_m": float(r["avg_ws"]), "length_m": float(r["avg_ln"])}
        for r in adg_result.mappings().all()
    }

    cap = compute_zone_capacity_units(zone_coords, adg_dims, buffer_ft, parking_mode)

    mode_entry = {
        "total_units": cap["total_units"],
        "max_by_adg": {str(k): v for k, v in cap["max_by_adg"].items()},
        "adg_weights": {str(k): v for k, v in cap["adg_weights"].items()},
        "buffer_ft": buffer_ft,
    }

    existing = await db.execute(
        text("SELECT capacity_data FROM zones WHERE id = :id"),
        {"id": zone_id},
    )
    existing_row = existing.mappings().first()
    merged = {}
    if existing_row and existing_row.get("capacity_data"):
        existing_data = existing_row["capacity_data"]
        if isinstance(existing_data, str):
            existing_data = json_mod.loads(existing_data)
        merged = dict(existing_data)
    merged[parking_mode] = mode_entry

    await db.execute(
        text("UPDATE zones SET capacity_data = :data, updated_at = NOW() WHERE id = :id"),
        {"data": json_mod.dumps(merged), "id": zone_id},
    )
    return merged


def _row_to_read(row) -> ZoneRead:
    coords = wkt_to_frontend_coords(str(row["geometry"]))
    return ZoneRead(
        id=row["id"],
        label=row["label"],
        color=row["color"],
        coordinates=coords,
        ramp_id=row["ramp_id"],
        area_sqft=row.get("area_sqft"),
        capacity=row.get("capacity"),
        capacity_data=row.get("capacity_data"),
    )


@router.get("", response_model=list[ZoneRead])
async def list_zones(
    ramp_id: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if ramp_id:
        result = await db.execute(
            text(f"SELECT {_SELECT_COLS} FROM zones WHERE ramp_id = :ramp_id ORDER BY created_at"),
            {"ramp_id": str(ramp_id)},
        )
    else:
        result = await db.execute(
            text(f"SELECT {_SELECT_COLS} FROM zones ORDER BY created_at")
        )
    return [_row_to_read(r) for r in result.mappings().all()]


@router.get("/{zone_id}", response_model=ZoneRead)
async def get_zone(zone_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text(f"SELECT {_SELECT_COLS} FROM zones WHERE id = :id"),
        {"id": str(zone_id)},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Zone not found")
    return _row_to_read(row)


@router.post("", response_model=ZoneRead, status_code=201)
async def create_zone(body: ZoneCreate, db: AsyncSession = Depends(get_db)):
    # Verify parent ramp exists
    ramp = await db.execute(
        text("SELECT id FROM ramps WHERE id = :id"),
        {"id": str(body.ramp_id)},
    )
    if not ramp.mappings().first():
        raise HTTPException(status_code=404, detail="Parent ramp not found")

    wkt = frontend_coords_to_wkt(body.coordinates)
    new_id = str(uuid7())
    await db.execute(
        text(
            """
            INSERT INTO zones (id, label, color, geometry, ramp_id, area_sqft, capacity)
            VALUES (:id, :label, :color, ST_GeomFromText(:wkt, 4326), :ramp_id, :area_sqft, :capacity)
            """
        ),
        {
            "id": new_id,
            "label": body.label,
            "color": body.color,
            "wkt": wkt,
            "ramp_id": str(body.ramp_id),
            "area_sqft": body.area_sqft,
            "capacity": body.capacity,
        },
    )

    try:
        await _compute_and_store_capacity(
            db, new_id, body.coordinates, body.buffer_ft, body.parking_mode
        )
    except Exception as e:
        # Don't fail the create if capacity compute breaks — cache will populate lazily.
        print(f"[create_zone] capacity precompute failed for {new_id}: {e}")

    await db.commit()
    result = await db.execute(
        text(f"SELECT {_SELECT_COLS} FROM zones WHERE id = :id"),
        {"id": new_id},
    )
    return _row_to_read(result.mappings().first())


@router.patch("/{zone_id}", response_model=ZoneRead)
async def update_zone(zone_id: UUID, body: ZoneUpdate, db: AsyncSession = Depends(get_db)):
    updates = {}
    params = {"id": str(zone_id)}

    for field in ("label", "color", "area_sqft", "capacity"):
        val = getattr(body, field)
        if val is not None:
            updates[field] = val
            params[field] = val

    if body.coordinates is not None:
        wkt = frontend_coords_to_wkt(body.coordinates)
        updates["geometry"] = "ST_GeomFromText(:wkt, 4326)"
        params["wkt"] = wkt
        # Clear cached capacity when geometry changes
        updates["capacity_data"] = None
        params["capacity_data"] = None

    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update")

    set_clauses = []
    for k, v in updates.items():
        if k == "geometry":
            set_clauses.append(f"geometry = {v}")
        else:
            set_clauses.append(f"{k} = :{k}")
    set_clauses.append("updated_at = NOW()")

    await db.execute(
        text(f"UPDATE zones SET {', '.join(set_clauses)} WHERE id = :id"),
        params,
    )
    await db.commit()
    result = await db.execute(
        text(f"SELECT {_SELECT_COLS} FROM zones WHERE id = :id"),
        {"id": str(zone_id)},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Zone not found")
    return _row_to_read(row)


@router.delete("/{zone_id}", status_code=204)
async def delete_zone(zone_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("DELETE FROM zones WHERE id = :id"), {"id": str(zone_id)}
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Zone not found")


class RecalculateRequest(BaseModel):
    buffer_ft: float = 5.0
    parking_mode: str = "hangar"


@router.post("/{zone_id}/recalculate-capacity")
async def recalculate_capacity(zone_id: UUID, body: RecalculateRequest, db: AsyncSession = Depends(get_db)):
    """Manually trigger capacity recomputation and cache in DB."""
    result = await db.execute(
        text("SELECT id, ST_AsText(geometry) AS geometry FROM zones WHERE id = :id"),
        {"id": str(zone_id)},
    )
    zone_row = result.mappings().first()
    if not zone_row:
        raise HTTPException(status_code=404, detail="Zone not found")

    zone_coords = wkt_to_frontend_coords(str(zone_row["geometry"]))
    capacity_data = await _compute_and_store_capacity(
        db, str(zone_id), zone_coords, body.buffer_ft, body.parking_mode
    )
    await db.commit()

    return {"status": "ok", "capacity_data": capacity_data}
