import re
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .access_control import ensure_shop_access
from .database import shops
from .locations import ensure_city_and_locality
from .login import get_current_user
from .roles import UserRole, get_user_role
from .session import CatalogReader, is_junction_session
from .shop_types import SHOP_TYPES, ShopTypeInfo
from .utils import parse_object_id

router = APIRouter(prefix="/shops", tags=["shops"])

_TIME_HH_MM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _strip_required(value: str, field_name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be blank")
    return value


def _validate_hh_mm(value: str, field_name: str) -> str:
    value = value.strip()
    if not _TIME_HH_MM.fullmatch(value):
        raise ValueError(f"{field_name} must be HH:MM in 24-hour format (e.g. 09:30)")
    return value


class ShopCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    city: str = Field(min_length=1, max_length=80)
    locality: str = Field(min_length=1, max_length=120)
    open_time: str = Field(min_length=4, max_length=5, description="Shop open time HH:MM (24h)")
    closed_time: str = Field(min_length=4, max_length=5, description="Shop closed time HH:MM (24h)")
    is_open: bool = True

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return _strip_required(value, "name")

    @field_validator("city")
    @classmethod
    def strip_city(cls, value: str) -> str:
        return _strip_required(value, "city")

    @field_validator("locality")
    @classmethod
    def strip_locality(cls, value: str) -> str:
        return _strip_required(value, "locality")

    @field_validator("open_time")
    @classmethod
    def validate_open_time(cls, value: str) -> str:
        return _validate_hh_mm(value, "open_time")

    @field_validator("closed_time")
    @classmethod
    def validate_closed_time(cls, value: str) -> str:
        return _validate_hh_mm(value, "closed_time")


class ShopUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    city: str | None = Field(default=None, min_length=1, max_length=80)
    locality: str | None = Field(default=None, min_length=1, max_length=120)
    open_time: str | None = Field(default=None, min_length=4, max_length=5)
    closed_time: str | None = Field(default=None, min_length=4, max_length=5)
    is_open: bool | None = None

    @field_validator("name", "city", "locality")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("open_time", "closed_time")
    @classmethod
    def validate_optional_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_hh_mm(value, "time")


class ShopOpenStatusUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    is_open: bool

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return _strip_required(value, "name")


class Shop(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    city: str
    locality: str
    open_time: str | None = None
    closed_time: str | None = None
    is_open: bool = True
    phone_number: str
    owner_user_id: str
    created_at: datetime
    updated_at: datetime


def serialize_shop(document: dict) -> Shop:
    return Shop(
        id=str(document["_id"]),
        name=document["name"],
        city=document.get("city", ""),
        locality=document.get("locality", ""),
        open_time=document.get("open_time"),
        closed_time=document.get("closed_time"),
        is_open=bool(document.get("is_open", True)),
        phone_number=document["phone_number"],
        owner_user_id=document["owner_user_id"],
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


def get_user_phone_number(user: dict) -> str:
    phone_number = user.get("phone_number")
    if not phone_number:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A verified phone number is required before creating a shop",
        )
    return phone_number


def find_owned_shop_by_name(user: dict, shop_name: str) -> dict:
    name = shop_name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="shop name must not be blank")

    escaped = re.escape(name)
    query: dict = {"name": {"$regex": f"^{escaped}$", "$options": "i"}}
    role = get_user_role(user)
    if role != UserRole.admin:
        query["owner_user_id"] = str(user["_id"])

    documents = list(shops.find(query).sort("created_at", -1))
    if not documents:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No shops found with this name")
    if len(documents) > 1 and role == UserRole.admin:
        # Prefer exact owner match ambiguity message for admins with duplicate names across owners
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Multiple shops share this name; use shop id to update open status",
        )
    return documents[0]


@router.get("/types", response_model=list[ShopTypeInfo])
def list_shop_types(_: CatalogReader) -> list[ShopTypeInfo]:
    """Shop types for owner app (user JWT) or junction.today (session JWT)."""
    return SHOP_TYPES


@router.get("", response_model=list[Shop])
def list_shops(auth: CatalogReader) -> list[Shop]:
    """
    List shops.
    - User JWT: owner sees own shops; admin sees all.
    - junction.today session JWT: public catalog of all shops.
    """
    if is_junction_session(auth):
        documents = shops.find({}).sort("created_at", -1)
        return [serialize_shop(document) for document in documents]

    current_user = auth["user"]
    role = get_user_role(current_user)
    query = {} if role == UserRole.admin else {"owner_user_id": str(current_user["_id"])}
    documents = shops.find(query).sort("created_at", -1)
    return [serialize_shop(document) for document in documents]


@router.get("/by-name/{shop_name}", response_model=list[Shop])
def get_shops_by_name(shop_name: str, auth: CatalogReader) -> list[Shop]:
    name = shop_name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="shop_name must not be blank")

    escaped = re.escape(name)
    query: dict = {"name": {"$regex": f"^{escaped}$", "$options": "i"}}
    if not is_junction_session(auth):
        current_user = auth["user"]
        role = get_user_role(current_user)
        if role != UserRole.admin:
            query["owner_user_id"] = str(current_user["_id"])

    documents = list(shops.find(query).sort("created_at", -1))
    if not documents:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No shops found with this name")
    return [serialize_shop(document) for document in documents]


@router.put("/open-status", response_model=Shop)
def update_shop_open_status(
    payload: ShopOpenStatusUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Shop:
    """Set whether a shop is open or closed by shop name. Body: { "name": "...", "is_open": true|false }."""
    existing = find_owned_shop_by_name(current_user, payload.name)
    ensure_shop_access(current_user, existing)
    document = shops.find_one_and_update(
        {"_id": existing["_id"]},
        {"$set": {"is_open": payload.is_open, "updated_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )
    return serialize_shop(document)


@router.get("/{shop_id}", response_model=Shop)
def get_shop(shop_id: str, auth: CatalogReader) -> Shop:
    document = shops.find_one({"_id": parse_object_id(shop_id, "Shop")})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    if not is_junction_session(auth):
        ensure_shop_access(auth["user"], document)
    return serialize_shop(document)


@router.post("", response_model=Shop, status_code=status.HTTP_201_CREATED)
def create_shop(payload: ShopCreate, current_user: Annotated[dict, Depends(get_current_user)]) -> Shop:
    shops.create_index([("owner_user_id", 1), ("name", 1)], unique=True)
    shops.create_index("phone_number", unique=True)

    city, locality = ensure_city_and_locality(payload.city, payload.locality)
    phone_number = get_user_phone_number(current_user)
    now = datetime.now(timezone.utc)
    document = {
        "name": payload.name,
        "city": city,
        "locality": locality,
        "open_time": payload.open_time,
        "closed_time": payload.closed_time,
        "is_open": payload.is_open,
        "phone_number": phone_number,
        "owner_user_id": str(current_user["_id"]),
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = shops.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A shop with this name or phone number already exists",
        ) from exc
    document["_id"] = result.inserted_id
    return serialize_shop(document)


@router.put("/{shop_id}", response_model=Shop)
def update_shop(
    shop_id: str,
    payload: ShopUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Shop:
    existing = shops.find_one({"_id": parse_object_id(shop_id, "Shop")})
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    ensure_shop_access(current_user, existing)

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide at least one field to update")

    city = changes.get("city", existing.get("city", ""))
    locality = changes.get("locality", existing.get("locality", ""))
    if "city" in changes or "locality" in changes:
        city, locality = ensure_city_and_locality(city, locality)
        changes["city"] = city
        changes["locality"] = locality

    changes["updated_at"] = datetime.now(timezone.utc)
    try:
        document = shops.find_one_and_update(
            {"_id": existing["_id"]},
            {"$set": changes},
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A shop with this name already exists") from exc
    return serialize_shop(document)


@router.delete("/{shop_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shop(shop_id: str, current_user: Annotated[dict, Depends(get_current_user)]) -> Response:
    existing = shops.find_one({"_id": parse_object_id(shop_id, "Shop")})
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    ensure_shop_access(current_user, existing)
    shops.delete_one({"_id": existing["_id"]})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
