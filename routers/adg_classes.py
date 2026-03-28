from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from database import get_db
from schemas import AdgClassRead, AdgClassUpdate

router = APIRouter(prefix="/api/adg-classes", tags=["adg_classes"])


@router.get("", response_model=list[AdgClassRead])
async def list_adg_classes(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("SELECT class, label, wingspan_min_m, wingspan_max_m, tail_height_min_m, tail_height_max_m, description FROM adg_classes ORDER BY class")
    )
    return [dict(r) for r in result.mappings().all()]


@router.patch("/{adg_class}", response_model=AdgClassRead)
async def update_adg_class(adg_class: int, body: AdgClassUpdate, db: AsyncSession = Depends(get_db)):
    updates = {}
    params = {"cls": adg_class}
    for field in ("label", "wingspan_min_m", "wingspan_max_m", "tail_height_min_m", "tail_height_max_m", "description"):
        val = getattr(body, field)
        if val is not None:
            updates[field] = val
            params[field] = val

    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update")

    set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
    await db.execute(
        text(f"UPDATE adg_classes SET {set_clauses}, updated_at = NOW() WHERE class = :cls"),
        params,
    )
    await db.commit()
    result = await db.execute(
        text("SELECT class, label, wingspan_min_m, wingspan_max_m, tail_height_min_m, tail_height_max_m, description FROM adg_classes WHERE class = :cls"),
        {"cls": adg_class},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="ADG class not found")
    return dict(row)
