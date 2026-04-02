from __future__ import annotations
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field


# ── ADG Classes ───────────────────────────────────────────────────────────

class AdgClassRead(BaseModel):
    class_: int = Field(alias="class")
    label: str
    wingspan_min_m: float
    wingspan_max_m: float
    tail_height_min_m: float
    tail_height_max_m: float
    description: Optional[str] = None

    model_config = {"from_attributes": True, "populate_by_name": True}


class AdgClassUpdate(BaseModel):
    label: Optional[str] = None
    wingspan_min_m: Optional[float] = None
    wingspan_max_m: Optional[float] = None
    tail_height_min_m: Optional[float] = None
    tail_height_max_m: Optional[float] = None
    description: Optional[str] = None


# ── Aircraft Types ────────────────────────────────────────────────────────

class AircraftTypeCreate(BaseModel):
    make: str
    model: str
    designator: Optional[str] = None
    wingspan_m: float
    length_m: Optional[float] = None
    tail_height_m: Optional[float] = None
    adg_class: int


class AircraftTypeUpdate(BaseModel):
    make: Optional[str] = None
    model: Optional[str] = None
    designator: Optional[str] = None
    wingspan_m: Optional[float] = None
    length_m: Optional[float] = None
    tail_height_m: Optional[float] = None
    adg_class: Optional[int] = None


class AircraftTypeRead(BaseModel):
    id: UUID
    make: str
    model: str
    designator: Optional[str] = None
    wingspan_m: float
    length_m: Optional[float] = None
    tail_height_m: Optional[float] = None
    adg_class: int

    model_config = {"from_attributes": True}


# ── Ramps ─────────────────────────────────────────────────────────────────

class RampCreate(BaseModel):
    label: str
    color: str = "#3b82f6"
    coordinates: list[list[float]]
    tenant: Optional[str] = None
    tenant_id: Optional[str] = None
    is_fbo: Optional[bool] = None
    area_sqft: Optional[float] = None
    capacity: Optional[int] = None
    is_terminal: Optional[bool] = None


class RampUpdate(BaseModel):
    label: Optional[str] = None
    color: Optional[str] = None
    coordinates: Optional[list[list[float]]] = None
    tenant: Optional[str] = None
    tenant_id: Optional[str] = None
    is_fbo: Optional[bool] = None
    area_sqft: Optional[float] = None
    capacity: Optional[int] = None
    is_terminal: Optional[bool] = None


class RampRead(BaseModel):
    id: UUID
    label: str
    color: str
    coordinates: list[list[float]]
    tenant: Optional[str] = None
    is_fbo: Optional[bool] = None
    area_sqft: Optional[float] = None
    capacity: Optional[int] = None
    is_terminal: Optional[bool] = None

    model_config = {"from_attributes": True}


# ── Zones ─────────────────────────────────────────────────────────────────

class ZoneCreate(BaseModel):
    label: str
    color: str = "#22c55e"
    coordinates: list[list[float]]
    ramp_id: UUID
    area_sqft: Optional[float] = None
    capacity: Optional[int] = None


class ZoneUpdate(BaseModel):
    label: Optional[str] = None
    color: Optional[str] = None
    coordinates: Optional[list[list[float]]] = None
    area_sqft: Optional[float] = None
    capacity: Optional[int] = None


class ZoneRead(BaseModel):
    id: UUID
    label: str
    color: str
    coordinates: list[list[float]]
    ramp_id: UUID
    area_sqft: Optional[float] = None
    capacity: Optional[int] = None

    model_config = {"from_attributes": True}


# ── Aircraft ──────────────────────────────────────────────────────────────

class AircraftCreate(BaseModel):
    tail_number: str
    type_id: UUID
    operator: str
    lat: float
    lng: float
    adg_class: int
    heading: float = 0.0
    zone_id: Optional[UUID] = None


class AircraftMoveRequest(BaseModel):
    lat: float
    lng: float
    heading: float = 0.0
    zone_id: Optional[UUID] = None


class AircraftRead(BaseModel):
    id: UUID
    tail_number: str
    type_id: UUID
    make: str
    model: str
    operator: str
    lat: float
    lng: float
    adg_class: int
    heading: float = 0.0
    zone_id: Optional[UUID] = None
    wingspan_m: float
    length_m: Optional[float] = None
    highlighted: bool = False

    model_config = {"from_attributes": True}
