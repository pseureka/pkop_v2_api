"""
AutoStack API — compute optimal aircraft placement within a zone.
"""

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional

from database import get_db
from utils.autostack import autostack
from utils.clearance import ClearancePolicyOverride, policy_from_request
from utils.geometry import wkt_to_frontend_coords

router = APIRouter(prefix="/api/autostack", tags=["autostack"])


class AutoStackRequest(BaseModel):
    zone_id: UUID
    aircraft: list[dict]  # [{ tail_number, wingspan_m, length_m, adg_class }]
    buffer_ft: float = 5.0
    headings: Optional[list[float]] = None
    num_options: int = 3
    clearance_policy: Optional[ClearancePolicyOverride] = None


class AutoStackFromZoneRequest(BaseModel):
    zone_id: UUID
    buffer_ft: float = 5.0
    headings: Optional[list[float]] = None
    num_options: int = 3
    parking_mode: str = "hangar"
    clearance_policy: Optional[ClearancePolicyOverride] = None


@router.post("/compute")
async def compute_autostack(body: AutoStackRequest, db: AsyncSession = Depends(get_db)):
    """
    Compute optimal placement for a list of aircraft within a zone.
    Aircraft list is provided in the request body.
    """
    # Get zone polygon
    result = await db.execute(
        text("SELECT ST_AsText(geometry) AS wkt FROM zones WHERE id = :zid"),
        {"zid": str(body.zone_id)},
    )
    zone_row = result.mappings().first()
    if not zone_row:
        raise HTTPException(status_code=404, detail="Zone not found")

    zone_coords = wkt_to_frontend_coords(zone_row["wkt"])

    # Enrich aircraft with dimensions from aircraft_types
    enriched = []
    for ac in body.aircraft:
        ws = ac.get("wingspan_m")
        ln = ac.get("length_m")

        if not ws and ac.get("type_id"):
            type_result = await db.execute(
                text("SELECT wingspan_m, length_m FROM aircraft_types WHERE id = :id"),
                {"id": ac["type_id"]},
            )
            type_row = type_result.mappings().first()
            if type_row:
                ws = type_row["wingspan_m"]
                ln = type_row.get("length_m")

        if not ws or not ln:
            continue  # Skip aircraft without real dimensions

        enriched.append({
            "tail_number": ac.get("tail_number", ""),
            "wingspan_m": ws,
            "length_m": ln,
            "adg_class": ac.get("adg_class", 2),
        })

    policy = policy_from_request(body.buffer_ft, body.clearance_policy)
    options = autostack(
        zone_coords,
        enriched,
        headings_to_try=body.headings,
        num_options=body.num_options,
        policy=policy,
    )

    return {
        "zone_id": str(body.zone_id),
        "options": options,
        "aircraft_count": len(enriched),
    }


@router.post("/zone/{zone_id}")
async def autostack_existing(zone_id: UUID, body: AutoStackFromZoneRequest, db: AsyncSession = Depends(get_db)):
    """
    Re-arrange all aircraft currently in the given zone for optimal placement.
    """
    # Get zone polygon
    result = await db.execute(
        text("SELECT ST_AsText(geometry) AS wkt FROM zones WHERE id = :zid"),
        {"zid": str(zone_id)},
    )
    zone_row = result.mappings().first()
    if not zone_row:
        raise HTTPException(status_code=404, detail="Zone not found")

    zone_coords = wkt_to_frontend_coords(zone_row["wkt"])
    parking_mode = body.parking_mode

    # Get aircraft in zone with dimensions from aircraft_types
    ac_result = await db.execute(
        text("""
            SELECT a.tail_number, a.adg_class, t.wingspan_m, t.length_m
            FROM aircraft a
            JOIN aircraft_types t ON a.type_id = t.id
            WHERE a.zone_id = :zid
            ORDER BY t.wingspan_m DESC NULLS LAST
        """),
        {"zid": str(zone_id)},
    )
    zone_aircraft = ac_result.mappings().all()

    if not zone_aircraft:
        return {"zone_id": str(zone_id), "options": [], "aircraft_count": 0,
                "message": "No aircraft in this zone"}

    aircraft_list = []
    for ac in zone_aircraft:
        ws = ac["wingspan_m"]
        ln = ac.get("length_m") or ws * 0.85
        aircraft_list.append({
            "tail_number": ac["tail_number"],
            "wingspan_m": ws,
            "length_m": ln,
            "adg_class": ac["adg_class"],
        })

    # Get ADG average dims for capacity unit calculation
    adg_result = await db.execute(text(
        """SELECT adg_class, AVG(wingspan_m) AS avg_ws,
                  AVG(COALESCE(length_m, wingspan_m * 0.85)) AS avg_ln
           FROM aircraft_types GROUP BY adg_class ORDER BY adg_class"""
    ))
    adg_dims = {
        int(r["adg_class"]): {"wingspan_m": float(r["avg_ws"]), "length_m": float(r["avg_ln"])}
        for r in adg_result.mappings().all()
    }

    policy = policy_from_request(body.buffer_ft, body.clearance_policy)
    options = autostack(
        zone_coords,
        aircraft_list,
        headings_to_try=body.headings,
        num_options=body.num_options,
        adg_dims=adg_dims,
        parking_mode=parking_mode,
        policy=policy,
    )

    return {
        "zone_id": str(zone_id),
        "options": options,
        "aircraft_count": len(aircraft_list),
    }
