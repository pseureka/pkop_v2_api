from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from pydantic import BaseModel

from database import get_db
from utils.geometry import wkt_to_frontend_coords
from utils.optimizer import (
    compute_zone_capacity_units,
    _greedy_fill, _greedy_fill_with_positions,
    _build_parked_obbs, OPERATIONAL_HEADINGS,
)
from utils.autostack import autostack as run_autostack
from utils.collision import _lat_lng_to_meters, FT_TO_M
from utils.autostack import _polygon_centroid, _polygon_bounds

router = APIRouter(prefix="/api/utilization", tags=["utilization"])

ADG_LABELS = {1: "ADG-I", 2: "ADG-II", 3: "ADG-III", 4: "ADG-IV", 5: "ADG-V", 6: "ADG-VI"}


async def _get_adg_dims(db):
    """Get average dimensions per ADG class from aircraft_types table."""
    result = await db.execute(text(
        """SELECT adg_class,
                  AVG(wingspan_m) AS avg_ws,
                  AVG(COALESCE(length_m, wingspan_m * 0.85)) AS avg_ln
           FROM aircraft_types GROUP BY adg_class ORDER BY adg_class"""
    ))
    return {
        int(r["adg_class"]): {"wingspan_m": float(r["avg_ws"]), "length_m": float(r["avg_ln"])}
        for r in result.mappings().all()
    }


@router.get("")
async def get_utilization(
    buffer_ft: float = Query(5.0, ge=0, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Utilization based on optimizer-derived ADG-I equivalent units.

    For each zone: total_units = max ADG-I count on empty zone.
    Each parked aircraft consumes units based on its ADG weight.
    Utilization = consumed_units / total_units × 100.
    """

    zones_result = await db.execute(text(
        "SELECT id, label, ramp_id, ST_AsText(geometry) AS geometry FROM zones ORDER BY created_at"
    ))
    zones_rows = zones_result.mappings().all()

    ramps_result = await db.execute(text(
        "SELECT id, label FROM ramps ORDER BY created_at"
    ))
    ramps_rows = {str(r["id"]): r["label"] for r in ramps_result.mappings().all()}

    ac_result = await db.execute(text(
        """SELECT a.id, a.zone_id, a.adg_class, t.wingspan_m, t.length_m
           FROM aircraft a
           JOIN aircraft_types t ON a.type_id = t.id
           WHERE a.zone_id IS NOT NULL"""
    ))
    ac_rows = ac_result.mappings().all()

    ac_by_zone = {}
    for ac in ac_rows:
        zid = str(ac["zone_id"])
        ac_by_zone.setdefault(zid, []).append(ac)

    adg_dims = await _get_adg_dims(db)

    zone_utils = []
    for z in zones_rows:
        zid = str(z["id"])
        coords = wkt_to_frontend_coords(str(z["geometry"]))
        parked = ac_by_zone.get(zid, [])
        aircraft_count = len(parked)

        # Compute capacity units for this zone
        cap = compute_zone_capacity_units(coords, adg_dims, buffer_ft)
        total_units = cap["total_units"]
        adg_weights = cap["adg_weights"]

        # Consumed units = sum of weight per parked aircraft's ADG class
        consumed_units = 0
        for ac in parked:
            cls = int(ac["adg_class"])
            consumed_units += adg_weights.get(cls, 0)
        consumed_units = round(consumed_units, 2)

        utilization_pct = round(
            (consumed_units / total_units * 100) if total_units > 0 else 0, 1)

        zone_utils.append({
            "zone_id": zid,
            "zone_label": z["label"],
            "ramp_id": str(z["ramp_id"]),
            "aircraft_count": aircraft_count,
            "total_units": total_units,
            "consumed_units": consumed_units,
            "utilization_pct": min(utilization_pct, 100),
        })

    # Aggregate per ramp
    ramp_map = {}
    for zu in zone_utils:
        rid = zu["ramp_id"]
        if rid not in ramp_map:
            ramp_map[rid] = {
                "ramp_id": rid,
                "ramp_label": ramps_rows.get(rid, "Unknown"),
                "aircraft_count": 0,
                "total_units": 0,
                "consumed_units": 0,
                "zones": [],
            }
        ramp_map[rid]["aircraft_count"] += zu["aircraft_count"]
        ramp_map[rid]["total_units"] += zu["total_units"]
        ramp_map[rid]["consumed_units"] += zu["consumed_units"]
        ramp_map[rid]["zones"].append(zu)

    ramps = []
    for r in ramp_map.values():
        r["utilization_pct"] = round(
            (r["consumed_units"] / r["total_units"] * 100)
            if r["total_units"] > 0 else 0, 1)
        ramps.append(r)

    total_units = sum(r["total_units"] for r in ramps)
    total_consumed = sum(r["consumed_units"] for r in ramps)
    overall = round((total_consumed / total_units * 100) if total_units > 0 else 0, 1)

    ramps_measured = sum(1 for r in ramps if r["total_units"] > 0)
    ramps_total = len(ramps_rows)

    return {
        "overall_utilization_pct": overall,
        "ramps_measured": ramps_measured,
        "ramps_total": ramps_total,
        "ramps": ramps,
    }


@router.get("/zones/{zone_id}/capacity")
async def get_zone_capacity(
    zone_id: UUID,
    buffer_ft: float = Query(5.0, ge=0, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Full capacity breakdown for a zone — max fit, weights, remaining per ADG class."""

    zone_result = await db.execute(
        text("SELECT id, ST_AsText(geometry) AS geometry FROM zones WHERE id = :id"),
        {"id": str(zone_id)},
    )
    zone_row = zone_result.mappings().first()
    if not zone_row:
        raise HTTPException(status_code=404, detail="Zone not found")

    zone_coords = wkt_to_frontend_coords(str(zone_row["geometry"]))

    ac_result = await db.execute(text(
        """SELECT a.lat, a.lng, a.heading, a.adg_class, t.wingspan_m, t.length_m
           FROM aircraft a JOIN aircraft_types t ON a.type_id = t.id
           WHERE a.zone_id = :zone_id"""),
        {"zone_id": str(zone_id)},
    )
    parked = [dict(r) for r in ac_result.mappings().all()]

    adg_dims = await _get_adg_dims(db)

    # Capacity units (max per ADG on empty zone)
    cap = compute_zone_capacity_units(zone_coords, adg_dims, buffer_ft)
    total_units = cap["total_units"]
    max_by_adg = cap["max_by_adg"]
    adg_weights = cap["adg_weights"]

    # Consumed units by parked aircraft
    consumed_units = 0
    parked_counts = {}  # adg_class -> count
    for ac in parked:
        cls = int(ac["adg_class"])
        consumed_units += adg_weights.get(cls, 0)
        parked_counts[cls] = parked_counts.get(cls, 0) + 1
    consumed_units = round(consumed_units, 2)
    available_units = round(max(total_units - consumed_units, 0), 2)

    utilization_pct = round(
        (consumed_units / total_units * 100) if total_units > 0 else 0, 1)

    # Parked breakdown — only ADG classes that are actually present
    parked_by_adg = []
    for cls in sorted(parked_counts.keys()):
        count = parked_counts[cls]
        weight = adg_weights.get(cls, 0)
        parked_by_adg.append({
            "adg_class": cls,
            "label": ADG_LABELS.get(cls, f"ADG-{cls}"),
            "count": count,
            "weight": weight,
            "units_consumed": round(count * weight, 2),
        })

    # Max capacity table — all ADG classes with remaining
    adg_table = []
    for cls in sorted(adg_dims.keys()):
        weight = adg_weights.get(cls, 0)
        remaining = int(available_units / weight) if weight > 0 else 0
        adg_table.append({
            "adg_class": cls,
            "label": ADG_LABELS.get(cls, f"ADG-{cls}"),
            "max_fit": max_by_adg.get(cls, 0),
            "weight": weight,
            "remaining": remaining,
        })

    return {
        "zone_id": str(zone_id),
        "total_units": total_units,
        "consumed_units": consumed_units,
        "available_units": available_units,
        "utilization_pct": min(utilization_pct, 100),
        "current_aircraft_count": len(parked),
        "parked_by_adg": parked_by_adg,
        "adg_table": adg_table,
    }


class OptimizeAnalysisRequest(BaseModel):
    buffer_ft: float = 5.0


@router.post("/zones/{zone_id}/optimize-analysis")
async def optimize_analysis(
    zone_id: UUID,
    body: OptimizeAnalysisRequest,
    db: AsyncSession = Depends(get_db),
):
    """Compare current vs optimized arrangement — shows AutoStack value.

    Returns fits_now (unit-based) and fits_after_autostack (spatial, after optimization).
    """
    zone_result = await db.execute(
        text("SELECT id, ST_AsText(geometry) AS geometry, parking_mode FROM zones WHERE id = :id"),
        {"id": str(zone_id)},
    )
    zone_row = zone_result.mappings().first()
    if not zone_row:
        raise HTTPException(status_code=404, detail="Zone not found")

    zone_coords = wkt_to_frontend_coords(str(zone_row["geometry"]))
    parking_mode = zone_row.get("parking_mode") or "hangar"

    ac_result = await db.execute(text(
        """SELECT a.tail_number, a.lat, a.lng, a.heading, a.adg_class,
                  t.wingspan_m, t.length_m
           FROM aircraft a JOIN aircraft_types t ON a.type_id = t.id
           WHERE a.zone_id = :zone_id"""),
        {"zone_id": str(zone_id)},
    )
    parked = [dict(r) for r in ac_result.mappings().all()]

    adg_dims = await _get_adg_dims(db)
    buffer_m = body.buffer_ft * FT_TO_M

    # Capacity units for "fits now" (unit-based, instant)
    cap = compute_zone_capacity_units(zone_coords, adg_dims, body.buffer_ft)
    adg_weights = cap["adg_weights"]
    total_units = cap["total_units"]

    consumed = sum(adg_weights.get(int(ac["adg_class"]), 0) for ac in parked)
    available = max(total_units - consumed, 0)

    fits_now = {}
    for cls, dims in adg_dims.items():
        w = adg_weights.get(cls, 0)
        fits_now[cls] = int(available / w) if w > 0 else 0

    # Run AutoStack on current aircraft → optimized positions
    aircraft_list = [
        {"tail_number": ac["tail_number"], "wingspan_m": ac["wingspan_m"],
         "length_m": ac["length_m"], "adg_class": ac["adg_class"]}
        for ac in parked if ac.get("wingspan_m") and ac.get("length_m")
    ]

    if not aircraft_list:
        return {
            "zone_id": str(zone_id),
            "fits_now": fits_now,
            "fits_after_autostack": fits_now,
            "improvement_total": 0,
        }

    # Use autostack() with strategies — same path as the AutoStack panel
    options = run_autostack(
        zone_coords, aircraft_list, buffer_ft=body.buffer_ft,
        num_options=1, adg_dims=adg_dims, parking_mode=parking_mode,
    )
    autostack_result = options[0] if options else {"placements": []}

    # Build OBBs from optimized positions
    ref_lat, ref_lng = _polygon_centroid(zone_coords)
    zone_m = [_lat_lng_to_meters(c[0], c[1], ref_lat, ref_lng) for c in zone_coords]
    zone_bounds = _polygon_bounds(zone_m)

    optimized_obbs = _build_parked_obbs(
        autostack_result["placements"], ref_lat, ref_lng, buffer_m)

    # Greedy fill per ADG on optimized arrangement
    fits_after = {}
    for cls, dims in adg_dims.items():
        ws = dims["wingspan_m"]
        ln = dims["length_m"]
        if ws <= 0 or ln <= 0:
            fits_after[cls] = 0
            continue
        fits_after[cls] = _greedy_fill(
            zone_m, zone_bounds, optimized_obbs,
            ws, ln, OPERATIONAL_HEADINGS, buffer_m, max_count=30)

    total_now = sum(fits_now.values())
    total_after = sum(fits_after.values())
    improvement = max(total_after - total_now, 0)

    # Build preview placements for map visualization
    preview_placements = []

    # Optimized positions of real aircraft (blue ghosts)
    for p in autostack_result["placements"]:
        preview_placements.append({**p, "type": "optimized"})

    # Additional ADG-I aircraft filling remaining space (green ghosts)
    if 1 in adg_dims:
        dims1 = adg_dims[1]
        additional = _greedy_fill_with_positions(
            zone_m, zone_bounds, optimized_obbs,
            dims1["wingspan_m"], dims1["length_m"],
            OPERATIONAL_HEADINGS, buffer_m,
            max_count=20, ref_lat=ref_lat, ref_lng=ref_lng, adg_class=1)
        for i, pos in enumerate(additional):
            preview_placements.append({
                **pos,
                "tail_number": f"PREVIEW_{i+1}",
                "type": "additional",
            })

    return {
        "zone_id": str(zone_id),
        "fits_now": fits_now,
        "fits_after_autostack": fits_after,
        "improvement_total": improvement,
        "preview_placements": preview_placements,
    }
