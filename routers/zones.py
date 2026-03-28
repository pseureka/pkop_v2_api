from uuid import UUID
from uuid_utils import uuid7
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional

from database import get_db
from schemas import ZoneCreate, ZoneUpdate, ZoneRead
from utils.geometry import frontend_coords_to_wkt, wkt_to_frontend_coords

router = APIRouter(prefix="/api/zones", tags=["zones"])

_SELECT_COLS = "id, label, color, ramp_id, area_sqft, capacity, ST_AsText(geometry) AS geometry"


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
