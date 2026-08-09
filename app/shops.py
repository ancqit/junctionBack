import re
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .database import shops
from .login import get_current_user
from .roles import UserRole, get_user_role
from .utils import parse_object_id

router = APIRouter(prefix="/shops", tags=["shops"])


class ShopCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class ShopUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class Shop(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    phone_number: str
    owner_user_id: str
    created_at: datetime
    updated_at: datetime


def serialize_shop(document: dict) -> Shop:
    return Shop(
        id=str(document["_id"]),
        name=document["name"],
        phone_number=document["phone_number"],
        owner_user_id=document["owner_user_id"],
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


def ensure_shop_access(user: dict, shop: dict) -> None:
    role = get_user_role(user)
    if role == UserRole.admin:
        return
    if str(shop.get("owner_user_id")) != str(user["_id"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this shop")


def get_user_phone_number(user: dict) -> str:
    phone_number = user.get("phone_number")
    if not phone_number:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A verified phone number is required before creating a shop",
        )
    return phone_number


@router.get("", response_model=list[Shop])
def list_shops(current_user: Annotated[dict, Depends(get_current_user)]) -> list[Shop]:
    role = get_user_role(current_user)
    query = {} if role == UserRole.admin else {"owner_user_id": str(current_user["_id"])}
    documents = shops.find(query).sort("created_at", -1)
    return [serialize_shop(document) for document in documents]


@router.get("/by-name/{shop_name}", response_model=list[Shop])
def get_shops_by_name(shop_name: str, current_user: Annotated[dict, Depends(get_current_user)]) -> list[Shop]:
    name = shop_name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="shop_name must not be blank")

    escaped = re.escape(name)
    query: dict = {"name": {"$regex": f"^{escaped}$", "$options": "i"}}
    role = get_user_role(current_user)
    if role != UserRole.admin:
        query["owner_user_id"] = str(current_user["_id"])

    documents = list(shops.find(query).sort("created_at", -1))
    if not documents:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No shops found with this name")
    return [serialize_shop(document) for document in documents]


@router.get("/{shop_id}", response_model=Shop)
def get_shop(shop_id: str, current_user: Annotated[dict, Depends(get_current_user)]) -> Shop:
    document = shops.find_one({"_id": parse_object_id(shop_id, "Shop")})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    ensure_shop_access(current_user, document)
    return serialize_shop(document)


@router.post("", response_model=Shop, status_code=status.HTTP_201_CREATED)
def create_shop(payload: ShopCreate, current_user: Annotated[dict, Depends(get_current_user)]) -> Shop:
    shops.create_index([("owner_user_id", 1), ("name", 1)], unique=True)
    shops.create_index("phone_number", unique=True)

    phone_number = get_user_phone_number(current_user)
    now = datetime.now(timezone.utc)
    document = {
        "name": payload.name,
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
def update_shop_name(
    shop_id: str,
    payload: ShopUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Shop:
    existing = shops.find_one({"_id": parse_object_id(shop_id, "Shop")})
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    ensure_shop_access(current_user, existing)

    try:
        document = shops.find_one_and_update(
            {"_id": existing["_id"]},
            {"$set": {"name": payload.name, "updated_at": datetime.now(timezone.utc)}},
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
