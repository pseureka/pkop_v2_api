from uuid import UUID
from uuid_utils import uuid7
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from database import get_db
from schemas import GeofenceCreate, GeofenceRead
from utils.geometry import frontend_coords_to_wkt, wkt_to_frontend_coords

router = APIRouter(prefix="/api/geofences", tags=["geofences"])


def _row_to_read(row) -> GeofenceRead:
    wkt = row.geometry
    if hasattr(wkt, "desc"):  # GeoAlchemy2 WKBElement
        wkt = wkt.desc
    coords = wkt_to_frontend_coords(str(wkt))
    return GeofenceRead(
        id=row.id,
        label=row.label,
        color=row.color,
        coordinates=coords,
        tenant=row.tenant,
        is_fbo=row.is_fbo,
        area_sqft=row.area_sqft,
        capacity=row.capacity,
    )


@router.get("", response_model=list[GeofenceRead])
async def list_geofences(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("SELECT id, label, tenant, is_fbo, color, area_sqft, capacity, ST_AsText(geometry) AS geometry FROM geofences ORDER BY created_at")
    )
    rows = result.mappings().all()
    return [_row_to_read(r) for r in rows]


@router.post("", response_model=GeofenceRead, status_code=201)
async def create_geofence(body: GeofenceCreate, db: AsyncSession = Depends(get_db)):
    wkt = frontend_coords_to_wkt(body.coordinates)
    new_id = str(uuid7())
    await db.execute(
        text(
            """
            INSERT INTO geofences (id, label, tenant, tenant_id, is_fbo, color, geometry, area_sqft, capacity, is_terminal)
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
        text("SELECT id, label, tenant, is_fbo, color, area_sqft, capacity, ST_AsText(geometry) AS geometry FROM geofences WHERE id = :id"),
        {"id": new_id},
    )
    row = result.mappings().first()
    return _row_to_read(row)


@router.delete("/{geofence_id}", status_code=204)
async def delete_geofence(geofence_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("DELETE FROM geofences WHERE id = :id"), {"id": str(geofence_id)}
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Geofence not found")
