from uuid import UUID
from uuid_utils import uuid7
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional

from database import get_db
from schemas import AircraftTypeCreate, AircraftTypeUpdate, AircraftTypeRead

router = APIRouter(prefix="/api/aircraft-types", tags=["aircraft_types"])

_SELECT_COLS = "id, make, model, designator, wingspan_m, length_m, adg_class"


@router.get("", response_model=list[AircraftTypeRead])
async def list_aircraft_types(
    adg_class: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if adg_class:
        result = await db.execute(
            text(f"SELECT {_SELECT_COLS} FROM aircraft_types WHERE adg_class = :cls ORDER BY make, model"),
            {"cls": adg_class},
        )
    else:
        result = await db.execute(
            text(f"SELECT {_SELECT_COLS} FROM aircraft_types ORDER BY make, model")
        )
    return [dict(r) for r in result.mappings().all()]


@router.post("", response_model=AircraftTypeRead, status_code=201)
async def create_aircraft_type(body: AircraftTypeCreate, db: AsyncSession = Depends(get_db)):
    new_id = str(uuid7())
    await db.execute(
        text(
            """
            INSERT INTO aircraft_types (id, make, model, designator, wingspan_m, length_m, adg_class)
            VALUES (:id, :make, :model, :designator, :wingspan_m, :length_m, :adg_class)
            """
        ),
        {
            "id": new_id,
            "make": body.make,
            "model": body.model,
            "designator": body.designator,
            "wingspan_m": body.wingspan_m,
            "length_m": body.length_m,
            "adg_class": body.adg_class,
        },
    )
    await db.commit()
    result = await db.execute(
        text(f"SELECT {_SELECT_COLS} FROM aircraft_types WHERE id = :id"),
        {"id": new_id},
    )
    return dict(result.mappings().first())


@router.patch("/{type_id}", response_model=AircraftTypeRead)
async def update_aircraft_type(type_id: UUID, body: AircraftTypeUpdate, db: AsyncSession = Depends(get_db)):
    updates = {}
    params = {"id": str(type_id)}
    for field in ("make", "model", "designator", "wingspan_m", "length_m", "adg_class"):
        val = getattr(body, field)
        if val is not None:
            updates[field] = val
            params[field] = val

    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update")

    set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
    await db.execute(
        text(f"UPDATE aircraft_types SET {set_clauses}, updated_at = NOW() WHERE id = :id"),
        params,
    )
    await db.commit()
    result = await db.execute(
        text(f"SELECT {_SELECT_COLS} FROM aircraft_types WHERE id = :id"),
        {"id": str(type_id)},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Aircraft type not found")
    return dict(row)


@router.delete("/{type_id}", status_code=204)
async def delete_aircraft_type(type_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("DELETE FROM aircraft_types WHERE id = :id"), {"id": str(type_id)}
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Aircraft type not found")
