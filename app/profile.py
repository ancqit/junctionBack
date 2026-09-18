from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from gridfs import GridFS
from gridfs.errors import NoFile
from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator, model_validator
from pymongo import ReturnDocument

from .database import database, notices, shops, users
from .access_control import ensure_shop_access, get_shop_by_store_id, resolve_store_id
from .login import get_current_user
from .product_images import validate_image_upload
from .utils import parse_object_id

router = APIRouter(prefix="/profile", tags=["profile"])
notices_router = APIRouter(prefix="/notices", tags=["notices"])

profile_avatar_fs = GridFS(database, collection="profile_avatars")

# Shop-owned profile fields (per active shop). Account identity stays on the user.

class Profile(BaseModel):
    id: str
    email: EmailStr | None = None
    phone_number: str | None = None
    display_name: str
    bio: str | None
    avatar_url: str | None
    digilocker_verified: bool = False
    digilocker_name: str | None = None
    aadhaar_verified: bool = False
    aadhaar_last4: str | None = None
    aadhaar_name: str | None = None
    gstin: str | None = None
    gst_verified: bool = False
    gst_legal_name: str | None = None
    gst_trade_name: str | None = None
    gst_status: str | None = None
    # Present when profile is scoped to an active shop.
    shop_id: str | None = None
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


def resolve_profile_shop(user: dict, shop_id: str | None) -> dict | None:
    """Load shop for profile when shop_id is provided; None keeps legacy user profile."""
    resolved = (shop_id or "").strip()
    if not resolved:
        return None
    shop = get_shop_by_store_id(resolved)
    ensure_shop_access(user, shop)
    return shop


def _pick_profile_value(shop: dict | None, user: dict, key: str, default=None):
    """Prefer shop-stored value when the key was set on the shop; else owner (legacy)."""
    if shop is not None and key in shop and shop.get(key) is not None:
        return shop.get(key)
    return user.get(key, default)


def _strip_optional(value) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None


def serialize_profile(user: dict, shop: dict | None = None) -> Profile:
    shop_id = str(shop["_id"]) if shop is not None else None

    # Soft content: prefer shop, fall back to owner for older records.
    bio = _strip_optional(_pick_profile_value(shop, user, "bio"))
    avatar_url = _strip_optional(_pick_profile_value(shop, user, "avatar_url"))

    # Verification is per shop when shop_id is present — do not inherit from the phone/user.
    if shop is not None:
        digilocker_verified = bool(shop.get("digilocker_verified", False))
        digilocker_name = _strip_optional(shop.get("digilocker_name"))
        aadhaar_verified = bool(shop.get("aadhaar_verified", False)) or digilocker_verified
        aadhaar_last4 = _strip_optional(shop.get("aadhaar_last4"))
        aadhaar_name = _strip_optional(shop.get("aadhaar_name")) or digilocker_name
        gst_verified = bool(shop.get("gst_verified", False))
        gstin = _strip_optional(shop.get("gstin"))
        gst_legal_name = _strip_optional(shop.get("gst_legal_name"))
        gst_trade_name = _strip_optional(shop.get("gst_trade_name"))
        gst_status = _strip_optional(shop.get("gst_status"))
        updated_at = shop.get("updated_at") or user["updated_at"]
    else:
        digilocker_verified = bool(user.get("digilocker_verified", False))
        digilocker_name = _strip_optional(user.get("digilocker_name"))
        aadhaar_verified = bool(user.get("aadhaar_verified", False)) or digilocker_verified
        aadhaar_last4 = _strip_optional(user.get("aadhaar_last4"))
        aadhaar_name = _strip_optional(user.get("aadhaar_name")) or digilocker_name
        gst_verified = bool(user.get("gst_verified", False))
        gstin = _strip_optional(user.get("gstin"))
        gst_legal_name = _strip_optional(user.get("gst_legal_name"))
        gst_trade_name = _strip_optional(user.get("gst_trade_name"))
        gst_status = _strip_optional(user.get("gst_status"))
        updated_at = user["updated_at"]

    return Profile(
        id=str(user["_id"]),
        email=user.get("email"),
        phone_number=user.get("phone_number"),
        display_name=user["display_name"],
        bio=bio,
        avatar_url=avatar_url,
        digilocker_verified=digilocker_verified,
        digilocker_name=digilocker_name,
        aadhaar_verified=aadhaar_verified,
        aadhaar_last4=aadhaar_last4,
        aadhaar_name=aadhaar_name,
        gstin=gstin,
        gst_verified=gst_verified,
        gst_legal_name=gst_legal_name,
        gst_trade_name=gst_trade_name,
        gst_status=gst_status,
        shop_id=shop_id,
        created_at=user["created_at"],
        updated_at=updated_at,
    )


@router.get("", response_model=Profile)
def read_profile(
    current_user: Annotated[dict, Depends(get_current_user)],
    shop_id: Annotated[str | None, Query(max_length=80)] = None,
    store_id: Annotated[str | None, Query(max_length=80, description="Alias of shop_id")] = None,
) -> Profile:
    shop = resolve_profile_shop(current_user, shop_id or store_id)
    return serialize_profile(current_user, shop)


@router.patch("", response_model=Profile)
def update_profile(
    payload: ProfileUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
    shop_id: Annotated[str | None, Query(max_length=80)] = None,
    store_id: Annotated[str | None, Query(max_length=80, description="Alias of shop_id")] = None,
) -> Profile:
    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        raise HTTPException(status_code=400, detail="Provide at least one profile field")

    shop = resolve_profile_shop(current_user, shop_id or store_id)
    now = datetime.now(timezone.utc)
    user = current_user

    # Account display name stays on the user (login identity).
    account_changes: dict = {}
    if "display_name" in changes:
        account_changes["display_name"] = changes.pop("display_name")
    if account_changes:
        account_changes["updated_at"] = now
        user = users.find_one_and_update(
            {"_id": current_user["_id"]},
            {"$set": account_changes},
            return_document=ReturnDocument.AFTER,
        )

    if shop is not None:
        shop_changes = {key: value for key, value in changes.items() if key in ("bio", "avatar_url")}
        if shop_changes:
            shop_changes["updated_at"] = now
            shop = shops.find_one_and_update(
                {"_id": shop["_id"]},
                {"$set": shop_changes},
                return_document=ReturnDocument.AFTER,
            )
        return serialize_profile(user, shop)

    # Legacy: no shop_id → keep writing shop fields on the user document.
    if changes:
        changes["updated_at"] = now
        user = users.find_one_and_update(
            {"_id": current_user["_id"]},
            {"$set": changes},
            return_document=ReturnDocument.AFTER,
        )
    return serialize_profile(user)


@router.post("/avatar", response_model=Profile, status_code=status.HTTP_200_OK)
async def upload_profile_avatar(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
    file: UploadFile = File(...),
    shop_id: Annotated[str | None, Query(max_length=80)] = None,
    store_id: Annotated[str | None, Query(max_length=80, description="Alias of shop_id")] = None,
) -> Profile:
    """Upload a shop profile photo. With shop_id, stores on the shop; else on the user (legacy)."""
    contents = await file.read()
    content_type = validate_image_upload(file, contents)
    shop = resolve_profile_shop(current_user, shop_id or store_id)
    file_id = profile_avatar_fs.put(
        contents,
        content_type=content_type,
        filename=file.filename or "avatar.jpg",
        metadata={
            "user_id": str(current_user["_id"]),
            "shop_id": str(shop["_id"]) if shop is not None else None,
            "source": "upload",
        },
    )
    base = str(request.base_url).rstrip("/")
    avatar_url = f"{base}/profile/avatar/file/{file_id}"
    now = datetime.now(timezone.utc)
    avatar_set = {
        "avatar_url": avatar_url,
        "avatar_stored_image_id": str(file_id),
        "updated_at": now,
    }

    if shop is not None:
        shop = shops.find_one_and_update(
            {"_id": shop["_id"]},
            {"$set": avatar_set},
            return_document=ReturnDocument.AFTER,
        )
        return serialize_profile(current_user, shop)

    user = users.find_one_and_update(
        {"_id": current_user["_id"]},
        {"$set": avatar_set},
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

    Full-day feed is sorted by updated_at descending (newest first), like a social feed.
    Each notice includes shop_name, city, and locality from the shop catalog.
    """
    notice_date = today_utc().isoformat()
    requested = (store_id or shop_id or "").strip()
    if requested:
        document = _find_today_notice(requested)
        return serialize_notices([document]) if document else []

    # Newest first — Facebook-style feed
    documents = list(notices.find({"notice_date": notice_date}).sort("updated_at", -1))
    return serialize_notices(documents)