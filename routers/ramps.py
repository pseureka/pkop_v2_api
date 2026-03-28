from uuid import UUID
from uuid_utils import uuid7
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from database import get_db
from schemas import RampCreate, RampUpdate, RampRead
from utils.geometry import frontend_coords_to_wkt, wkt_to_frontend_coords

router = APIRouter(prefix="/api/ramps", tags=["ramps"])

_SELECT_COLS = "id, label, tenant, tenant_id, is_fbo, color, area_sqft, capacity, is_terminal, ST_AsText(geometry) AS geometry"


def _row_to_read(row) -> RampRead:
    coords = wkt_to_frontend_coords(str(row["geometry"]))
    return RampRead(
        id=row["id"],
        label=row["label"],
        color=row["color"],
        coordinates=coords,
        tenant=row.get("tenant"),
        is_fbo=row.get("is_fbo"),
        area_sqft=row.get("area_sqft"),
        capacity=row.get("capacity"),
        is_terminal=row.get("is_terminal"),
    )


@router.get("", response_model=list[RampRead])
async def list_ramps(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text(f"SELECT {_SELECT_COLS} FROM ramps ORDER BY created_at")
    )
    return [_row_to_read(r) for r in result.mappings().all()]


@router.post("", response_model=RampRead, status_code=201)
async def create_ramp(body: RampCreate, db: AsyncSession = Depends(get_db)):
    wkt = frontend_coords_to_wkt(body.coordinates)
    new_id = str(uuid7())
    await db.execute(
        text(
            """
            INSERT INTO ramps (id, label, tenant, tenant_id, is_fbo, color, geometry, area_sqft, capacity, is_terminal)
            VALUES (:id, :label, :tenant, :tenant_id, :is_fbo, :color, ST_GeomFromText(:wkt, 4326), :area_sqft, :capacity, :is_terminal)
            """
        ),
        {
            "id": new_id,
            "label": body.label,
            "tenant": body.tenant,
            "tenant_id": body.tenant_id,
            "is_fbo": body.is_fbo,
            "color": body.color,
            "wkt": wkt,
            "area_sqft": body.area_sqft,
            "capacity": body.capacity,
            "is_terminal": body.is_terminal,
        },
    )
    await db.commit()
    result = await db.execute(
        text(f"SELECT {_SELECT_COLS} FROM ramps WHERE id = :id"),
        {"id": new_id},
    )
    return _row_to_read(result.mappings().first())


@router.patch("/{ramp_id}", response_model=RampRead)
async def update_ramp(ramp_id: UUID, body: RampUpdate, db: AsyncSession = Depends(get_db)):
    updates = {}
    params = {"id": str(ramp_id)}

    for field in ("label", "color", "tenant", "tenant_id", "is_fbo", "area_sqft", "capacity", "is_terminal"):
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
        text(f"UPDATE ramps SET {', '.join(set_clauses)} WHERE id = :id"),
        params,
    )
    await db.commit()
    result = await db.execute(
        text(f"SELECT {_SELECT_COLS} FROM ramps WHERE id = :id"),
        {"id": str(ramp_id)},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Ramp not found")
    return _row_to_read(row)


@router.delete("/{ramp_id}", status_code=204)
async def delete_ramp(ramp_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("DELETE FROM ramps WHERE id = :id"), {"id": str(ramp_id)}
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Ramp not found")
