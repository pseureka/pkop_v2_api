from uuid_utils import uuid7
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Boolean, Float, Integer, Text,
    TIMESTAMP, ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID
from geoalchemy2 import Geometry
from database import Base


def _now():
    return datetime.now(timezone.utc)


class AdgClass(Base):
    __tablename__ = "adg_classes"

    class_ = Column("class", Integer, primary_key=True)
    label = Column(Text, nullable=False)
    wingspan_min_m = Column(Float, nullable=False)
    wingspan_max_m = Column(Float, nullable=False)
    tail_height_min_m = Column(Float, nullable=False)
    tail_height_max_m = Column(Float, nullable=False)
    description = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), default=_now)
    updated_at = Column(TIMESTAMP(timezone=True), default=_now, onupdate=_now)


class AircraftType(Base):
    __tablename__ = "aircraft_types"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    make = Column(Text, nullable=False)
    model = Column(Text, nullable=False)
    designator = Column(Text)
    wingspan_m = Column(Float, nullable=False)
    length_m = Column(Float)
    tail_height_m = Column(Float)
    adg_class = Column(Integer, ForeignKey("adg_classes.class"), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), default=_now)
    updated_at = Column(TIMESTAMP(timezone=True), default=_now, onupdate=_now)


class Ramp(Base):
    __tablename__ = "ramps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    label = Column(Text, nullable=False)
    tenant = Column(Text)
    tenant_id = Column(Text)
    is_fbo = Column(Boolean)
    color = Column(Text, nullable=False, default="#3b82f6")
    geometry = Column(Geometry("POLYGON", srid=4326), nullable=False)
    area_sqft = Column(Float)
    capacity = Column(Integer)
    is_terminal = Column(Boolean)
    created_at = Column(TIMESTAMP(timezone=True), default=_now)
    created_by = Column(Text)
    updated_at = Column(TIMESTAMP(timezone=True), default=_now, onupdate=_now)
    updated_by = Column(Text)


class Zone(Base):
    __tablename__ = "zones"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    label = Column(Text, nullable=False)
    color = Column(Text, nullable=False, default="#22c55e")
    geometry = Column(Geometry("POLYGON", srid=4326), nullable=False)
    ramp_id = Column(
        UUID(as_uuid=True), ForeignKey("ramps.id", ondelete="CASCADE"), nullable=False
    )
    area_sqft = Column(Float)
    capacity = Column(Integer)
    created_at = Column(TIMESTAMP(timezone=True), default=_now)
    created_by = Column(Text)
    updated_at = Column(TIMESTAMP(timezone=True), default=_now, onupdate=_now)
    updated_by = Column(Text)


class Aircraft(Base):
    __tablename__ = "aircraft"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    tail_number = Column(Text, nullable=False, unique=True)
    type_id = Column(
        UUID(as_uuid=True), ForeignKey("aircraft_types.id"), nullable=False
    )
    operator = Column(Text, nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    adg_class = Column(Integer, ForeignKey("adg_classes.class"), nullable=False)
    heading = Column(Float, nullable=False, default=0.0)
    zone_id = Column(
        UUID(as_uuid=True), ForeignKey("zones.id", ondelete="SET NULL"), nullable=True
    )
    highlighted = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP(timezone=True), default=_now)
    created_by = Column(Text)
    updated_at = Column(TIMESTAMP(timezone=True), default=_now, onupdate=_now)
    updated_by = Column(Text)
