from uuid import UUID
from uuid_utils import uuid7
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from database import get_db
from schemas import AircraftCreate, AircraftMoveRequest, AircraftRead

router = APIRouter(prefix="/api/aircraft", tags=["aircraft"])

# JOIN aircraft_types for make, model, wingspan, length
_SELECT_QUERY = """
    SELECT a.id, a.tail_number, a.type_id, t.make, t.model,
           a.operator, a.lat, a.lng, a.adg_class, a.heading,
           a.zone_id, t.wingspan_m, t.length_m, a.highlighted
    FROM aircraft a
    JOIN aircraft_types t ON a.type_id = t.id
"""


def _row_to_read(row) -> AircraftRead:
    return AircraftRead(
        id=row["id"],
        tail_number=row["tail_number"],
        type_id=row["type_id"],
        make=row["make"],
        model=row["model"],
        operator=row["operator"],
        lat=row["lat"],
        lng=row["lng"],
        adg_class=row["adg_class"],
        heading=row.get("heading", 0.0),
        zone_id=row.get("zone_id"),
        wingspan_m=row["wingspan_m"],
        length_m=row.get("length_m"),
        highlighted=row.get("highlighted", False),
    )


@router.get("", response_model=list[AircraftRead])
async def list_aircraft(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text(f"{_SELECT_QUERY} ORDER BY a.created_at")
    )
    return [_row_to_read(r) for r in result.mappings().all()]


@router.post("", response_model=AircraftRead, status_code=201)
async def create_aircraft(body: AircraftCreate, db: AsyncSession = Depends(get_db)):
    # Validate type_id exists
    type_check = await db.execute(
        text("SELECT id FROM aircraft_types WHERE id = :id"),
        {"id": str(body.type_id)},
    )
    if not type_check.mappings().first():
        raise HTTPException(status_code=400, detail="Aircraft type not found")

    new_id = str(uuid7())
    await db.execute(
        text(
            """
            INSERT INTO aircraft (id, tail_number, type_id, operator, lat, lng, adg_class,
                                  heading, zone_id)
            VALUES (:id, :tail_number, :type_id, :operator, :lat, :lng, :adg_class,
                    :heading, :zone_id)
            """
        ),
        {
            "id": new_id,
            "tail_number": body.tail_number,
            "type_id": str(body.type_id),
            "operator": body.operator,
            "lat": body.lat,
            "lng": body.lng,
            "adg_class": body.adg_class,
            "heading": body.heading,
            "zone_id": str(body.zone_id) if body.zone_id else None,
        },
    )
    await db.commit()
    result = await db.execute(
        text(f"{_SELECT_QUERY} WHERE a.id = :id"),
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
            "UPDATE aircraft SET lat = :lat, lng = :lng, heading = :heading, "
            "zone_id = :zone_id, updated_at = NOW() WHERE id = :id"
        ),
        {
            "lat": body.lat,
            "lng": body.lng,
            "heading": body.heading,
            "zone_id": str(body.zone_id) if body.zone_id else None,
            "id": str(aircraft_id),
        },
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    result = await db.execute(
        text(f"{_SELECT_QUERY} WHERE a.id = :id"),
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
