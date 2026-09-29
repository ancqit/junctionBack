"""jEarth mushroom farm aggregator — growers, apartments, area search."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import uuid4

from pymongo import ReturnDocument
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from .admin import require_admin
from .database import earth_apartments, earth_growers

router = APIRouter(prefix="/earth/farm", tags=["earth-farm"])

CYCLE_STAGES = (
    "inoculation",
    "spawn-run",
    "pinning",
    "fruiting",
    "harvest",
    "rest",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(value: str) -> str:
    raw = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return (raw[:64] or uuid4().hex[:12])


def ensure_farm_indexes() -> None:
    earth_growers.create_index("id", unique=True)
    earth_growers.create_index("area_key")
    earth_apartments.create_index("id", unique=True)
    earth_apartments.create_index("area_key")


def _area_key(area: str) -> str:
    return re.sub(r"\s+", " ", (area or "").strip().lower())


def _serialize_grower(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "area": doc.get("area"),
        "city": doc.get("city"),
        "locality": doc.get("locality"),
        "crop_name": doc.get("crop_name") or "Oyster",
        "cycle_stage": doc.get("cycle_stage") or "spawn-run",
        "cycle_day": int(doc.get("cycle_day") or 0),
        "units_available": int(doc.get("units_available") or 0),
        "unit_price": float(doc.get("unit_price") or 0),
        "currency": doc.get("currency") or "INR",
        "notes": doc.get("notes") or "",
        "active": bool(doc.get("active", True)),
    }


def _serialize_apartment(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "area": doc.get("area"),
        "city": doc.get("city"),
        "locality": doc.get("locality"),
        "address": doc.get("address") or "",
        "active": bool(doc.get("active", True)),
    }


SEED_GROWERS: list[dict[str, Any]] = [
    {
        "id": "grower-indiranagar-oyster",
        "name": "Lane Oyster Co-op",
        "area": "Indiranagar, Bengaluru",
        "city": "Bengaluru",
        "locality": "Indiranagar",
        "crop_name": "Oyster",
        "cycle_stage": "fruiting",
        "cycle_day": 18,
        "units_available": 40,
        "unit_price": 120,
        "currency": "INR",
        "notes": "Shade-grown bags on paddy straw.",
        "active": True,
    },
    {
        "id": "grower-hsr-milky",
        "name": "HSR Milky Bags",
        "area": "HSR Layout, Bengaluru",
        "city": "Bengaluru",
        "locality": "HSR Layout",
        "crop_name": "Milky",
        "cycle_stage": "pinning",
        "cycle_day": 12,
        "units_available": 24,
        "unit_price": 140,
        "currency": "INR",
        "notes": "Apartment terrace grow room.",
        "active": True,
    },
]

SEED_APARTMENTS: list[dict[str, Any]] = [
    {
        "id": "apt-indiranagar-grove",
        "name": "Grove Residences",
        "area": "Indiranagar, Bengaluru",
        "city": "Bengaluru",
        "locality": "Indiranagar",
        "address": "100 Feet Rd",
        "active": True,
    },
    {
        "id": "apt-hsr-cedar",
        "name": "Cedar Heights",
        "area": "HSR Layout, Bengaluru",
        "city": "Bengaluru",
        "locality": "HSR Layout",
        "address": "27th Main",
        "active": True,
    },
]


def seed_farm_if_empty() -> None:
    if earth_growers.count_documents({}, limit=1) == 0:
        for row in SEED_GROWERS:
            earth_growers.insert_one({**row, "area_key": _area_key(row["area"]), "created_at": _now(), "updated_at": _now()})
    if earth_apartments.count_documents({}, limit=1) == 0:
        for row in SEED_APARTMENTS:
            earth_apartments.insert_one(
                {**row, "area_key": _area_key(row["area"]), "created_at": _now(), "updated_at": _now()}
            )


class GrowerOut(BaseModel):
    id: str
    name: str
    area: str
    city: str | None = None
    locality: str | None = None
    crop_name: str
    cycle_stage: str
    cycle_day: int
    units_available: int
    unit_price: float
    currency: str = "INR"
    notes: str = ""
    active: bool = True


class ApartmentOut(BaseModel):
    id: str
    name: str
    area: str
    city: str | None = None
    locality: str | None = None
    address: str = ""
    active: bool = True


class FarmSearchResponse(BaseModel):
    area: str
    growers: list[GrowerOut]
    apartments: list[ApartmentOut]


class GrowerCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    area: str = Field(min_length=2, max_length=160)
    city: str | None = Field(default=None, max_length=80)
    locality: str | None = Field(default=None, max_length=80)
    crop_name: str = Field(default="Oyster", max_length=80)
    cycle_stage: str = Field(default="spawn-run", max_length=40)
    cycle_day: int = Field(default=0, ge=0, le=365)
    units_available: int = Field(default=0, ge=0, le=100_000)
    unit_price: float = Field(default=0, ge=0)
    currency: str = Field(default="INR", max_length=8)
    notes: str = Field(default="", max_length=500)
    active: bool = True


class GrowerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    area: str | None = Field(default=None, min_length=2, max_length=160)
    city: str | None = Field(default=None, max_length=80)
    locality: str | None = Field(default=None, max_length=80)
    crop_name: str | None = Field(default=None, max_length=80)
    cycle_stage: str | None = Field(default=None, max_length=40)
    cycle_day: int | None = Field(default=None, ge=0, le=365)
    units_available: int | None = Field(default=None, ge=0, le=100_000)
    unit_price: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=8)
    notes: str | None = Field(default=None, max_length=500)
    active: bool | None = None


class ApartmentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    area: str = Field(min_length=2, max_length=160)
    city: str | None = Field(default=None, max_length=80)
    locality: str | None = Field(default=None, max_length=80)
    address: str = Field(default="", max_length=240)
    active: bool = True


class ApartmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    area: str | None = Field(default=None, min_length=2, max_length=160)
    city: str | None = Field(default=None, max_length=80)
    locality: str | None = Field(default=None, max_length=80)
    address: str | None = Field(default=None, max_length=240)
    active: bool | None = None


def _match_area(query: str) -> dict[str, Any]:
    key = _area_key(query)
    if not key:
        return {}
    pattern = re.compile(re.escape(key), re.IGNORECASE)
    return {
        "active": {"$ne": False},
        "$or": [
            {"area_key": {"$regex": pattern}},
            {"area": {"$regex": pattern}},
            {"city": {"$regex": pattern}},
            {"locality": {"$regex": pattern}},
        ],
    }


@router.get("/search", response_model=FarmSearchResponse)
def search_farm(area: str = Query(min_length=1, max_length=120)) -> FarmSearchResponse:
    try:
        ensure_farm_indexes()
        seed_farm_if_empty()
    except Exception:
        pass
    filt = _match_area(area)
    growers = [_serialize_grower(doc) for doc in earth_growers.find(filt, {"_id": 0}).limit(40)]
    apartments = [_serialize_apartment(doc) for doc in earth_apartments.find(filt, {"_id": 0}).limit(40)]
    return FarmSearchResponse(area=area.strip(), growers=growers, apartments=apartments)


@router.get("/growers/{grower_id}", response_model=GrowerOut)
def get_grower(grower_id: str) -> GrowerOut:
    doc = earth_growers.find_one({"id": grower_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grower not found")
    return GrowerOut(**_serialize_grower(doc))


@router.get("/apartments/{apartment_id}", response_model=ApartmentOut)
def get_apartment(apartment_id: str) -> ApartmentOut:
    doc = earth_apartments.find_one({"id": apartment_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Apartment not found")
    return ApartmentOut(**_serialize_apartment(doc))


@router.post("/admin/growers", response_model=GrowerOut, status_code=status.HTTP_201_CREATED)
def admin_create_grower(
    body: GrowerCreate,
    _admin: Annotated[dict, Depends(require_admin)],
) -> GrowerOut:
    ensure_farm_indexes()
    stage = body.cycle_stage if body.cycle_stage in CYCLE_STAGES else "spawn-run"
    entry_id = f"grower-{_slug(body.name)}-{uuid4().hex[:6]}"
    doc = {
        "id": entry_id,
        "name": body.name.strip(),
        "area": body.area.strip(),
        "area_key": _area_key(body.area),
        "city": (body.city or "").strip() or None,
        "locality": (body.locality or "").strip() or None,
        "crop_name": body.crop_name.strip() or "Oyster",
        "cycle_stage": stage,
        "cycle_day": body.cycle_day,
        "units_available": body.units_available,
        "unit_price": body.unit_price,
        "currency": body.currency.strip() or "INR",
        "notes": body.notes.strip(),
        "active": body.active,
        "created_at": _now(),
        "updated_at": _now(),
    }
    earth_growers.insert_one(doc)
    return GrowerOut(**_serialize_grower(doc))


@router.patch("/admin/growers/{grower_id}", response_model=GrowerOut)
def admin_update_grower(
    grower_id: str,
    body: GrowerUpdate,
    _admin: Annotated[dict, Depends(require_admin)],
) -> GrowerOut:
    updates = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "cycle_stage" in updates and updates["cycle_stage"] not in CYCLE_STAGES:
        updates["cycle_stage"] = "spawn-run"
    if "area" in updates:
        updates["area_key"] = _area_key(str(updates["area"]))
    if not updates:
        doc = earth_growers.find_one({"id": grower_id}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grower not found")
        return GrowerOut(**_serialize_grower(doc))
    updates["updated_at"] = _now()
    doc = earth_growers.find_one_and_update(
        {"id": grower_id},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grower not found")
    return GrowerOut(**_serialize_grower(doc))


@router.post("/admin/apartments", response_model=ApartmentOut, status_code=status.HTTP_201_CREATED)
def admin_create_apartment(
    body: ApartmentCreate,
    _admin: Annotated[dict, Depends(require_admin)],
) -> ApartmentOut:
    ensure_farm_indexes()
    entry_id = f"apt-{_slug(body.name)}-{uuid4().hex[:6]}"
    doc = {
        "id": entry_id,
        "name": body.name.strip(),
        "area": body.area.strip(),
        "area_key": _area_key(body.area),
        "city": (body.city or "").strip() or None,
        "locality": (body.locality or "").strip() or None,
        "address": body.address.strip(),
        "active": body.active,
        "created_at": _now(),
        "updated_at": _now(),
    }
    earth_apartments.insert_one(doc)
    return ApartmentOut(**_serialize_apartment(doc))


@router.patch("/admin/apartments/{apartment_id}", response_model=ApartmentOut)
def admin_update_apartment(
    apartment_id: str,
    body: ApartmentUpdate,
    _admin: Annotated[dict, Depends(require_admin)],
) -> ApartmentOut:
    updates = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "area" in updates:
        updates["area_key"] = _area_key(str(updates["area"]))
    if not updates:
        doc = earth_apartments.find_one({"id": apartment_id}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Apartment not found")
        return ApartmentOut(**_serialize_apartment(doc))
    updates["updated_at"] = _now()
    doc = earth_apartments.find_one_and_update(
        {"id": apartment_id},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Apartment not found")
    return ApartmentOut(**_serialize_apartment(doc))
