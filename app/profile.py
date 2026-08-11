from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator
from pymongo import ReturnDocument

from .database import notices, shops, users
from .access_control import AuthenticatedUser, ensure_shop_access, require_store_access
from .login import get_current_user
from .roles import UserRole, get_user_role
from .utils import parse_object_id

router = APIRouter(prefix="/profile", tags=["profile"])
notices_router = APIRouter(prefix="/notices", tags=["notices"])


class Profile(BaseModel):
    id: str
    email: EmailStr | None = None
    phone_number: str | None = None
    display_name: str
    bio: str | None
    avatar_url: str | None
    digilocker_verified: bool = False
    digilocker_name: str | None = None
    created_at: datetime
    updated_at: datetime


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    bio: str | None = Field(default=None, max_length=500)
    avatar_url: HttpUrl | None = None

    @field_validator("display_name")
    @classmethod
    def display_name_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("display_name must not be blank")
        return value


def serialize_profile(user: dict) -> Profile:
    return Profile(
        id=str(user["_id"]),
        email=user.get("email"),
        phone_number=user.get("phone_number"),
        display_name=user["display_name"],
        bio=user.get("bio"),
        avatar_url=user.get("avatar_url"),
        digilocker_verified=bool(user.get("digilocker_verified", False)),
        digilocker_name=user.get("digilocker_name"),
        created_at=user["created_at"],
        updated_at=user["updated_at"],
    )


@router.get("", response_model=Profile)
def read_profile(current_user: Annotated[dict, Depends(get_current_user)]) -> Profile:
    return serialize_profile(current_user)


@router.patch("", response_model=Profile)
def update_profile(payload: ProfileUpdate, current_user: Annotated[dict, Depends(get_current_user)]) -> Profile:
    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        raise HTTPException(status_code=400, detail="Provide at least one profile field")
    changes["updated_at"] = datetime.now(timezone.utc)
    user = users.find_one_and_update({"_id": current_user["_id"]}, {"$set": changes}, return_document=ReturnDocument.AFTER)
    return serialize_profile(user)


class NoticeCreate(BaseModel):
    store_id: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=1000)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value


class Notice(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    store_id: str
    message: str
    notice_date: date
    created_at: datetime
    updated_at: datetime


def serialize_notice(document: dict) -> Notice:
    raw_date = document["notice_date"]
    notice_date = date.fromisoformat(raw_date) if isinstance(raw_date, str) else raw_date
    return Notice(
        id=str(document["_id"]),
        store_id=document["store_id"],
        message=document["message"],
        notice_date=notice_date,
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


@notices_router.post("", response_model=Notice)
def post_today_notice(
    payload: NoticeCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Notice:
    shop = shops.find_one({"_id": parse_object_id(payload.store_id, "Shop")})
    if shop is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    ensure_shop_access(current_user, shop)

    notice_date = today_utc()
    now = datetime.now(timezone.utc)
    document = {
        "store_id": payload.store_id,
        "message": payload.message,
        "notice_date": notice_date.isoformat(),
        "owner_user_id": str(current_user["_id"]),
        "created_at": now,
        "updated_at": now,
    }

    notices.create_index([("store_id", 1), ("notice_date", 1)], unique=True)
    existing = notices.find_one({"store_id": payload.store_id, "notice_date": notice_date.isoformat()})
    if existing is None:
        result = notices.insert_one(document)
        document["_id"] = result.inserted_id
    else:
        updated = notices.find_one_and_update(
            {"_id": existing["_id"]},
            {"$set": {"message": payload.message, "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        document = updated

    return serialize_notice(document)


@notices_router.get("/today", response_model=Notice)
def get_today_notice(
    current_user: Annotated[dict, Depends(get_current_user)],
    store_id: Annotated[str, Query(min_length=1, max_length=80)],
) -> Notice:
    require_store_access(current_user, store_id)
    notice_date = today_utc().isoformat()
    document = notices.find_one({"store_id": store_id, "notice_date": notice_date})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No notice posted for today")
    return serialize_notice(document)


@notices_router.get("", response_model=list[Notice])
def list_today_notices(current_user: Annotated[dict, Depends(get_current_user)]) -> list[Notice]:
    notice_date = today_utc().isoformat()
    role = get_user_role(current_user)
    if role == UserRole.admin:
        documents = notices.find({"notice_date": notice_date}).sort("updated_at", -1)
    else:
        shop_ids = [str(shop["_id"]) for shop in shops.find({"owner_user_id": str(current_user["_id"])})]
        if not shop_ids:
            return []
        documents = notices.find({"store_id": {"$in": shop_ids}, "notice_date": notice_date}).sort("updated_at", -1)
    return [serialize_notice(document) for document in documents]