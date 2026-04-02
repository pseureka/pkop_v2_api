"""
Tail number lookup — searches our aircraft_types DB and optionally
proxies to external registries (FAA, etc.).
"""

import re
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional

from database import get_db

router = APIRouter(prefix="/api/lookup", tags=["lookup"])

# Country prefix → registry name
COUNTRY_PREFIXES = {
    "N": "FAA (United States)",
    "C-": "Transport Canada",
    "G-": "UK CAA",
    "D-": "Germany (LBA)",
    "F-": "France (DGAC)",
    "EC-": "Spain (AESA)",
    "I-": "Italy (ENAC)",
    "PH-": "Netherlands (ILT)",
    "OO-": "Belgium (BCAA)",
    "OE-": "Austria (ACG)",
    "HB-": "Switzerland (FOCA)",
    "PR-": "Brazil (ANAC)",
    "LV-": "Argentina (ANAC)",
    "CC-": "Chile (DGAC)",
    "VH-": "Australia (CASA)",
    "JA": "Japan (JCAB)",
    "HL": "South Korea (MOLIT)",
    "A6-": "UAE (GCAA)",
    "A9C-": "Bahrain (CAA)",
    "HZ-": "Saudi Arabia (GACA)",
    "VP-": "British Overseas Territories",
    "9H-": "Malta (TM-CAD)",
    "TC-": "Turkey (SHGM)",
    "SU-": "Egypt (ECAA)",
    "ZS-": "South Africa (SACAA)",
    "B-": "China/Taiwan (CAAC)",
    "VT-": "India (DGCA)",
    "9V-": "Singapore (CAAS)",
}


def detect_country(tail_number: str) -> dict:
    """Detect country from tail number prefix."""
    tn = tail_number.upper().strip()
    # Try longest prefixes first
    for prefix in sorted(COUNTRY_PREFIXES.keys(), key=len, reverse=True):
        if tn.startswith(prefix):
            return {"prefix": prefix, "registry": COUNTRY_PREFIXES[prefix], "country_code": prefix.rstrip("-")}
    return {"prefix": None, "registry": "Unknown", "country_code": None}


@router.get("/tail/{tail_number}")
async def lookup_tail(tail_number: str, db: AsyncSession = Depends(get_db)):
    """
    Look up aircraft info by tail number.
    1. Detect country from prefix
    2. Search our aircraft_types DB for matching type
    3. Return combined info
    """
    tn = tail_number.upper().strip()
    country = detect_country(tn)

    # Check if we already have this tail in our aircraft table
    result = await db.execute(
        text("""
            SELECT a.tail_number, a.operator, a.adg_class, a.type_id,
                   t.make, t.model, t.designator, t.wingspan_m, t.length_m
            FROM aircraft a
            JOIN aircraft_types t ON a.type_id = t.id
            WHERE a.tail_number = :tn
            LIMIT 1
        """),
        {"tn": tn},
    )
    existing = result.mappings().first()

    if existing:
        return {
            "tail_number": tn,
            "found": True,
            "source": "local_db",
            "country": country,
            "aircraft": {
                "type": f"{existing['make']} {existing['model']}",
                "type_id": str(existing["type_id"]),
                "operator": existing["operator"],
                "make": existing["make"],
                "model": existing["model"],
                "designator": existing.get("designator"),
                "adg_class": existing["adg_class"],
                "wingspan_m": existing["wingspan_m"],
                "length_m": existing.get("length_m"),
            },
        }

    return {
        "tail_number": tn,
        "found": False,
        "source": None,
        "country": country,
        "aircraft": None,
        "message": "Tail number not found in local database. Select aircraft type manually.",
    }


@router.get("/search")
async def search_aircraft_types(
    q: str = Query(..., min_length=1, description="Search query (make, model, or designator)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Full-text search across aircraft_types by make, model, or designator.
    Returns matching types with dimensions.
    """
    search = f"%{q.upper()}%"
    result = await db.execute(
        text("""
            SELECT id, make, model, designator, wingspan_m, length_m, adg_class
            FROM aircraft_types
            WHERE UPPER(make) LIKE :q
               OR UPPER(model) LIKE :q
               OR UPPER(designator) LIKE :q
            ORDER BY make, model
            LIMIT 20
        """),
        {"q": search},
    )
    rows = result.mappings().all()
    return [
        {
            "id": str(r["id"]),
            "make": r["make"],
            "model": r["model"],
            "designator": r.get("designator"),
            "wingspan_m": r["wingspan_m"],
            "length_m": r.get("length_m"),
            "adg_class": r["adg_class"],
            "label": f"{r['make']} {r['model']}",
        }
        for r in rows
    ]


@router.get("/countries")
async def list_countries():
    """Return all known aircraft registration country prefixes."""
    return [
        {"prefix": k, "registry": v}
        for k, v in sorted(COUNTRY_PREFIXES.items())
    ]
