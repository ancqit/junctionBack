from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from gridfs import GridFS
from gridfs.errors import NoFile
from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator, model_validator
from pymongo import ReturnDocument

from .database import database, notices, shops, users
from .access_control import ensure_shop_access, resolve_store_id
from .login import get_current_user
from .product_images import validate_image_upload
from .utils import parse_object_id

router = APIRouter(prefix="/profile", tags=["profile"])
notices_router = APIRouter(prefix="/notices", tags=["notices"])

profile_avatar_fs = GridFS(database, collection="profile_avatars")


class Profile(BaseModel):
    id: str
    email: EmailStr | None = None
    phone_number: str | None = None
    display_name: str
    bio: str | None
    avatar_url: str | None
    digilocker_verified: bool = False
    digilocker_name: str | None = None
    gstin: str | None = None
    gst_verified: bool = False
    gst_legal_name: str | None = None
    gst_trade_name: str | None = None
    gst_status: str | None = None
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
        gstin=user.get("gstin"),
        gst_verified=bool(user.get("gst_verified", False)),
        gst_legal_name=user.get("gst_legal_name"),
        gst_trade_name=user.get("gst_trade_name"),
        gst_status=user.get("gst_status"),
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


@router.post("/avatar", response_model=Profile, status_code=status.HTTP_200_OK)
async def upload_profile_avatar(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
    file: UploadFile = File(...),
) -> Profile:
    """Upload a shop/profile photo from device. Stores in GridFS and sets avatar_url."""
    contents = await file.read()
    content_type = validate_image_upload(file, contents)
    file_id = profile_avatar_fs.put(
        contents,
        content_type=content_type,
        filename=file.filename or "avatar.jpg",
        metadata={"user_id": str(current_user["_id"]), "source": "upload"},
    )
    base = str(request.base_url).rstrip("/")
    avatar_url = f"{base}/profile/avatar/file/{file_id}"
    user = users.find_one_and_update(
        {"_id": current_user["_id"]},
        {
            "$set": {
                "avatar_url": avatar_url,
                "avatar_stored_image_id": str(file_id),
                "updated_at": datetime.now(timezone.utc),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    return serialize_profile(user)


@router.get("/avatar/file/{stored_image_id}")
def get_profile_avatar_file(stored_image_id: str) -> StreamingResponse:
    """Public read for profile photos (used in <img src>)."""
    try:
        grid_out = profile_avatar_fs.get(parse_object_id(stored_image_id, "Avatar"))
    except (NoFile, Exception):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found")
    return StreamingResponse(
        grid_out,
        media_type=grid_out.content_type or "image/jpeg",
        headers={"Content-Disposition": f'inline; filename="{grid_out.filename or "avatar.jpg"}"'},
    )


class NoticeCreate(BaseModel):
    store_id: str | None = Field(default=None, min_length=1, max_length=80)
    shop_id: str | None = Field(default=None, min_length=1, max_length=80, description="Alias of store_id")
    message: str = Field(min_length=1, max_length=1000)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value

    @model_validator(mode="after")
    def require_shop_reference(self):
        if not ((self.store_id or "").strip() or (self.shop_id or "").strip()):
            raise ValueError("store_id or shop_id is required")
        return self


class Notice(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    store_id: str
    message: str
    notice_date: date
    created_at: datetime
    updated_at: datetime
    # Denormalized from the shop so the public board is junction-agnostic.
    shop_name: str | None = None
    city: str | None = None
    locality: str | None = None


def _shop_meta_for_store_ids(store_ids: set[str]) -> dict[str, dict[str, str]]:
    object_ids = []
    for store_id in store_ids:
        try:
            object_ids.append(parse_object_id(store_id, "Shop"))
        except HTTPException:
            continue
    if not object_ids:
        return {}
    meta: dict[str, dict[str, str]] = {}
    for document in shops.find({"_id": {"$in": object_ids}}):
        meta[str(document["_id"])] = {
            "shop_name": str(document.get("name") or "").strip(),
            "city": str(document.get("city") or "").strip(),
            "locality": str(document.get("locality") or "").strip(),
        }
    return meta


def serialize_notice(document: dict, shop_meta: dict[str, str] | None = None) -> Notice:
    raw_date = document["notice_date"]
    if isinstance(raw_date, datetime):
        notice_date = raw_date.date()
    elif isinstance(raw_date, date):
        notice_date = raw_date
    else:
        notice_date = date.fromisoformat(str(raw_date)[:10])
    meta = shop_meta or {}
    return Notice(
        id=str(document["_id"]),
        store_id=str(document["store_id"]),
        message=document["message"],
        notice_date=notice_date,
        created_at=document["created_at"],
        updated_at=document["updated_at"],
        shop_name=meta.get("shop_name") or None,
        city=meta.get("city") or None,
        locality=meta.get("locality") or None,
    )


def serialize_notices(documents: list[dict]) -> list[Notice]:
    store_ids = {str(document.get("store_id", "")).strip() for document in documents}
    store_ids.discard("")
    meta_by_store = _shop_meta_for_store_ids(store_ids)
    return [
        serialize_notice(document, meta_by_store.get(str(document.get("store_id", "")).strip()))
        for document in documents
    ]


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _find_today_notice(store_id: str) -> dict | None:
    # Always query with an ISO date string — Python date objects are not valid BSON
    # and make pymongo raise (500) on find.
    notice_date = today_utc().isoformat()
    store_id = store_id.strip()
    return notices.find_one({"store_id": store_id, "notice_date": notice_date})


@notices_router.post("", response_model=Notice)
def post_today_notice(
    payload: NoticeCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Notice:
    store_id = resolve_store_id(store_id=payload.store_id, shop_id=payload.shop_id)
    shop = shops.find_one({"_id": parse_object_id(store_id, "Shop")})
    if shop is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    ensure_shop_access(current_user, shop)

    notice_date = today_utc()
    now = datetime.now(timezone.utc)
    document = {
        "store_id": store_id,
        "message": payload.message,
        "notice_date": notice_date.isoformat(),
        "owner_user_id": str(current_user["_id"]),
        "created_at": now,
        "updated_at": now,
    }

    notices.create_index([("store_id", 1), ("notice_date", 1)], unique=True)
    existing = _find_today_notice(store_id)
    if existing is None:
        result = notices.insert_one(document)
        document["_id"] = result.inserted_id
    else:
        updated = notices.find_one_and_update(
            {"_id": existing["_id"]},
            {"$set": {"message": payload.message, "updated_at": now, "store_id": store_id, "notice_date": notice_date.isoformat()}},
            return_document=ReturnDocument.AFTER,
        )
        document = updated

    return serialize_notices([document])[0]


@notices_router.get("/today", response_model=list[Notice], openapi_extra={"security": []})
def get_today_notice(
    store_id: Annotated[str | None, Query(max_length=80)] = None,
    shop_id: Annotated[str | None, Query(max_length=80, description="Alias of store_id")] = None,
) -> list[Notice]:
    """
    Today's notice for a shop as a 0-or-1 list. Public — no JWT required.
    Missing/blank store_id or no notice for today → [].
    """
    resolved = (store_id or shop_id or "").strip()
    if not resolved:
        return []
    document = _find_today_notice(resolved)
    return serialize_notices([document]) if document else []


@notices_router.delete("/today", status_code=status.HTTP_204_NO_CONTENT)
def delete_today_notice(
    current_user: Annotated[dict, Depends(get_current_user)],
    store_id: Annotated[str | None, Query(max_length=80)] = None,
    shop_id: Annotated[str | None, Query(max_length=80, description="Alias of store_id")] = None,
) -> Response:
    """Delete today's notice for a shop. JWT required; shop ownership same as POST."""
    resolved = resolve_store_id(store_id=store_id, shop_id=shop_id)
    shop = shops.find_one({"_id": parse_object_id(resolved, "Shop")})
    if shop is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    ensure_shop_access(current_user, shop)

    existing = _find_today_notice(resolved)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notice not found")
    result = notices.delete_one({"_id": existing["_id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notice not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@notices_router.get("", response_model=list[Notice], openapi_extra={"security": []})
def list_today_notices(
    store_id: Annotated[str | None, Query(max_length=80)] = None,
    shop_id: Annotated[str | None, Query(max_length=80, description="Alias of store_id")] = None,
) -> list[Notice]:
    """
    Today's notices. Public and junction-agnostic. Optional store_id/shop_id filters to one shop.

    Full-day feed is sorted by updated_at ascending (FIFO queue: oldest first) so
    clients can take the last N entries as the recent notice-board window.
    Each notice includes shop_name, city, and locality from the shop catalog.
    """
    notice_date = today_utc().isoformat()
    requested = (store_id or shop_id or "").strip()
    if requested:
        document = _find_today_notice(requested)
        return serialize_notices([document]) if document else []

    # FIFO queue: oldest first — junction-agnostic global feed
    documents = list(notices.find({"notice_date": notice_date}).sort("updated_at", 1))
    return serialize_notices(documents)