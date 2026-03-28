from uuid import UUID
from uuid_utils import uuid7
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from database import get_db
from schemas import ParkingSpotCreate, ParkingSpotUpdate, ParkingSpotRead
from utils.geometry import frontend_coords_to_wkt, wkt_to_frontend_coords

router = APIRouter(prefix="/api/spots", tags=["parking_spots"])


def _row_to_read(row, occupied: bool = False) -> ParkingSpotRead:
    wkt = row["geometry"]
    coords = wkt_to_frontend_coords(str(wkt))
    classes = row["accepted_classes"]
    if isinstance(classes, str):
        classes = [int(c) for c in classes.strip("{}").split(",") if c]
    return ParkingSpotRead(
        id=row["id"],
        name=row["name"],
        coordinates=coords,
        accepted_classes=classes,
        zone_id=row.get("zone_id"),
        occupied=occupied,
    )


@router.get("", response_model=list[ParkingSpotRead])
async def list_spots(db: AsyncSession = Depends(get_db)):
    spots_result = await db.execute(
        text(
            "SELECT id, name, accepted_classes, zone_id, ST_AsText(geometry) AS geometry "
            "FROM parking_spots ORDER BY created_at"
        )
    )
    spots = spots_result.mappings().all()

    occ_result = await db.execute(
        text("SELECT DISTINCT spot_id FROM aircraft WHERE spot_id IS NOT NULL")
    )
    occupied_ids = {str(r["spot_id"]) for r in occ_result.mappings().all()}

    return [_row_to_read(s, occupied=str(s["id"]) in occupied_ids) for s in spots]


@router.post("", response_model=ParkingSpotRead, status_code=201)
async def create_spot(body: ParkingSpotCreate, db: AsyncSession = Depends(get_db)):
    wkt = frontend_coords_to_wkt(body.coordinates)
    new_id = str(uuid7())
    await db.execute(
        text(
            """
            INSERT INTO parking_spots (id, name, geometry, accepted_classes, zone_id)
            VALUES (:id, :name, ST_GeomFromText(:wkt, 4326), :classes, :zone_id)
            """
        ),
        {
            "id": new_id,
            "name": body.name,
            "wkt": wkt,
            "classes": body.accepted_classes,
            "zone_id": str(body.zone_id) if body.zone_id else None,
        },
    )
    await db.commit()
    result = await db.execute(
        text(
            "SELECT id, name, accepted_classes, zone_id, ST_AsText(geometry) AS geometry "
            "FROM parking_spots WHERE id = :id"
        ),
        {"id": new_id},
    )
    return _row_to_read(result.mappings().first())


@router.patch("/{spot_id}", response_model=ParkingSpotRead)
async def update_spot(spot_id: UUID, body: ParkingSpotUpdate, db: AsyncSession = Depends(get_db)):
    updates = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.accepted_classes is not None:
        updates["accepted_classes"] = body.accepted_classes
    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update")

    set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = str(spot_id)
    await db.execute(
        text(f"UPDATE parking_spots SET {set_clauses}, updated_at = NOW() WHERE id = :id"),
        updates,
    )
    await db.commit()
    result = await db.execute(
        text(
            "SELECT id, name, accepted_classes, zone_id, ST_AsText(geometry) AS geometry "
            "FROM parking_spots WHERE id = :id"
        ),
        {"id": str(spot_id)},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Spot not found")
    return _row_to_read(row)


@router.delete("/{spot_id}", status_code=204)
async def delete_spot(spot_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("DELETE FROM parking_spots WHERE id = :id"), {"id": str(spot_id)}
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Spot not found")
