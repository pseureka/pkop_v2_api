from uuid import UUID
from uuid_utils import uuid7
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from database import get_db
from schemas import AircraftCreate, AircraftMoveRequest, AircraftRead

router = APIRouter(prefix="/api/aircraft", tags=["aircraft"])


def _row_to_read(row) -> AircraftRead:
    return AircraftRead(
        id=row["id"],
        tail_number=row["tail_number"],
        type=row["type"],
        operator=row["operator"],
        lat=row["lat"],
        lng=row["lng"],
        adg_class=row["adg_class"],
        spot_id=row.get("spot_id"),
        highlighted=row.get("highlighted", False),
    )


@router.get("", response_model=list[AircraftRead])
async def list_aircraft(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("SELECT id, tail_number, type, operator, lat, lng, adg_class, spot_id, highlighted FROM aircraft ORDER BY created_at")
    )
    return [_row_to_read(r) for r in result.mappings().all()]


@router.post("", response_model=AircraftRead, status_code=201)
async def create_aircraft(body: AircraftCreate, db: AsyncSession = Depends(get_db)):
    new_id = str(uuid7())
    await db.execute(
        text(
            """
            INSERT INTO aircraft (id, tail_number, type, operator, lat, lng, adg_class, spot_id)
            VALUES (:id, :tail_number, :type, :operator, :lat, :lng, :adg_class, :spot_id)
            """
        ),
        {
            "id": new_id,
            "tail_number": body.tail_number,
            "type": body.type,
            "operator": body.operator,
            "lat": body.lat,
            "lng": body.lng,
            "adg_class": body.adg_class,
            "spot_id": str(body.spot_id) if body.spot_id else None,
        },
    )
    await db.commit()
    result = await db.execute(
        text("SELECT id, tail_number, type, operator, lat, lng, adg_class, spot_id, highlighted FROM aircraft WHERE id = :id"),
        {"id": new_id},
    )
    row = result.mappings().first()
    return _row_to_read(row)


@router.patch("/{aircraft_id}/move", response_model=AircraftRead)
async def move_aircraft(
    aircraft_id: UUID, body: AircraftMoveRequest, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        text(
            "UPDATE aircraft SET lat = :lat, lng = :lng, spot_id = :spot_id, updated_at = NOW() "
            "WHERE id = :id"
        ),
        {
            "lat": body.lat,
            "lng": body.lng,
            "spot_id": str(body.spot_id) if body.spot_id else None,
            "id": str(aircraft_id),
        },
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    result = await db.execute(
        text("SELECT id, tail_number, type, operator, lat, lng, adg_class, spot_id, highlighted FROM aircraft WHERE id = :id"),
        {"id": str(aircraft_id)},
    )
    row = result.mappings().first()
    return _row_to_read(row)


@router.delete("/{aircraft_id}", status_code=204)
async def delete_aircraft(aircraft_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("DELETE FROM aircraft WHERE id = :id"), {"id": str(aircraft_id)}
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Aircraft not found")
