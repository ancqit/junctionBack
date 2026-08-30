import re
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .access_control import ensure_shop_access
from bson import ObjectId

from .database import products, shops, users
from .locations import ensure_city_and_locality
from .login import get_current_user
from .plan_service import (
    PlanSummary,
    PlanType,
    build_shop_plan_summary,
    default_plan_document,
    select_plan_for_shop,
)
from .products import Product, serialize_product
from .roles import UserRole, get_user_role
from .session import CatalogReader, is_junction_session
from .shop_cleanup import delete_shop_cascade
from .shop_payments import PlanPurchaseRequest, ShopPayment, create_plan_purchase
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
    address: str | None = Field(default=None, max_length=240, description="Street / shop address line")
    open_time: str = Field(min_length=4, max_length=5, description="Shop open time HH:MM (24h)")
    closed_time: str = Field(min_length=4, max_length=5, description="Shop closed time HH:MM (24h)")
    is_open: bool = True
    show_phone: bool = False

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

    @field_validator("address")
    @classmethod
    def strip_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

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
    address: str | None = Field(default=None, max_length=240)
    open_time: str | None = Field(default=None, min_length=4, max_length=5)
    closed_time: str | None = Field(default=None, min_length=4, max_length=5)
    is_open: bool | None = None
    show_phone: bool | None = None

    @field_validator("name", "city", "locality")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("address")
    @classmethod
    def strip_optional_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

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


class ShopPhoneStatusUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    show_phone: bool

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return _strip_required(value, "name")


class ShopPlanSelectRequest(BaseModel):
    plan_type: PlanType


class Shop(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    city: str
    locality: str
    address: str | None = None
    open_time: str | None = None
    closed_time: str | None = None
    is_open: bool = True
    show_phone: bool = False
    phone_number: str | None = None
    owner_user_id: str
    shop_type: str | None = None
    shop_type_label: str | None = None
    owner_bio: str | None = None
    avatar_url: str | None = None
    plan: PlanSummary | None = None
    created_at: datetime
    updated_at: datetime


def _shop_type_label(value: str | None) -> str | None:
    if not value:
        return None
    trimmed = value.strip()
    for entry in SHOP_TYPES:
        if entry.value == trimmed:
            return entry.label
    return trimmed


def _owner_catalog_lookup(owner_user_ids: set[str]) -> dict[str, dict]:
    object_ids: list[ObjectId] = []
    for owner_id in owner_user_ids:
        if ObjectId.is_valid(owner_id):
            object_ids.append(ObjectId(owner_id))
    if not object_ids:
        return {}
    owners = users.find({"_id": {"$in": object_ids}})
    return {str(owner["_id"]): owner for owner in owners}


def serialize_shop(document: dict, owner: dict | None = None) -> Shop:
    plan_summary = None
    if document.get("plan") is not None:
        plan_summary = build_shop_plan_summary(document)
    address = document.get("address")
    if isinstance(address, str):
        address = address.strip() or None
    else:
        address = None
    phone = document.get("phone_number")
    if isinstance(phone, str):
        phone = phone.strip() or None
    else:
        phone = None
    if not phone and owner:
        owner_phone = owner.get("phone_number")
        if isinstance(owner_phone, str):
            phone = owner_phone.strip() or None
    shop_type_raw = document.get("shop_type")
    if owner and owner.get("shop_type"):
        shop_type_raw = owner.get("shop_type")
    shop_type = shop_type_raw.strip() if isinstance(shop_type_raw, str) and shop_type_raw.strip() else None
    owner_bio = owner.get("bio") if owner else None
    if isinstance(owner_bio, str):
        owner_bio = owner_bio.strip() or None
    else:
        owner_bio = None
    avatar_url = owner.get("avatar_url") if owner else None
    if isinstance(avatar_url, str):
        avatar_url = avatar_url.strip() or None
    else:
        avatar_url = None
    return Shop(
        id=str(document["_id"]),
        name=document["name"],
        city=document.get("city", ""),
        locality=document.get("locality", ""),
        address=address,
        open_time=document.get("open_time"),
        closed_time=document.get("closed_time"),
        is_open=bool(document.get("is_open", True)),
        show_phone=bool(document.get("show_phone", False)),
        phone_number=phone,
        owner_user_id=document["owner_user_id"],
        shop_type=shop_type,
        shop_type_label=_shop_type_label(shop_type),
        owner_bio=owner_bio,
        avatar_url=avatar_url,
        plan=plan_summary,
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


def serialize_shops(documents: list[dict]) -> list[Shop]:
    owner_ids = {str(document.get("owner_user_id", "")).strip() for document in documents}
    owner_ids.discard("")
    owners = _owner_catalog_lookup(owner_ids)
    return [
        serialize_shop(document, owners.get(str(document.get("owner_user_id", "")).strip()))
        for document in documents
    ]


def get_user_phone_number(user: dict) -> str:
    phone_number = user.get("phone_number")
    if not phone_number:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A verified phone number is required before creating a shop",
        )
    return phone_number


def ensure_shop_indexes() -> None:
    """
    One mobile/user may own many shops.
    Shop names must be unique per phone number (and per owner).
    """
    shops.create_index([("owner_user_id", 1), ("name", 1)], unique=True)
    shops.create_index([("phone_number", 1), ("name", 1)], unique=True)
    shops.create_index("owner_user_id")
    # Drop legacy unique phone index if present (blocked multi-shop per number).
    for index in shops.list_indexes():
        if index.get("name") == "phone_number_1" and index.get("unique"):
            shops.drop_index("phone_number_1")
            break
    shops.create_index("phone_number")


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
def list_shops(
    auth: CatalogReader,
    shop_id: str | None = Query(default=None, max_length=80, description="Return this shop only"),
    store_id: str | None = Query(default=None, max_length=80, description="Alias of shop_id"),
) -> list[Shop]:
    """
    List shops.
    - User JWT: owner sees own shops; admin sees all.
    - junction.today session JWT: public catalog of all shops.
    - Optional shop_id/store_id query returns that one shop.
    Shop JSON includes show_phone (boolean switch) and phone_number (never nulled by the switch).
    """
    requested = (shop_id or store_id or "").strip()
    if shop_id and store_id and shop_id.strip() != store_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="shop_id and store_id must match when both are provided",
        )
    if requested:
        document = shops.find_one({"_id": parse_object_id(requested, "Shop")})
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
        if not is_junction_session(auth):
            ensure_shop_access(auth["user"], document)
        return serialize_shops([document])

    if is_junction_session(auth):
        documents = shops.find({}).sort("created_at", -1)
        return serialize_shops(list(documents))

    current_user = auth["user"]
    role = get_user_role(current_user)
    query = {} if role == UserRole.admin else {"owner_user_id": str(current_user["_id"])}
    documents = shops.find(query).sort("created_at", -1)
    return serialize_shops(documents)


@router.get("/by-name/{shop_name}", response_model=list[Shop])
def get_shops_by_name(
    shop_name: str,
    auth: CatalogReader,
) -> list[Shop]:
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
    return serialize_shops(documents)


@router.get("/by-location", response_model=list[Shop])
def list_shops_by_location(
    auth: CatalogReader,
    city: str = Query(..., min_length=1, max_length=80),
    locality: str = Query(..., min_length=1, max_length=120),
) -> list[Shop]:
    """
    List shops in a city + locality.
    Intended for junction.today (session JWT); also works with owner/admin user JWT.
    """
    city_name = city.strip()
    locality_name = locality.strip()
    if not city_name or not locality_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="city and locality are required",
        )

    query: dict = {
        "city": {"$regex": f"^{re.escape(city_name)}$", "$options": "i"},
        "locality": {"$regex": f"^{re.escape(locality_name)}$", "$options": "i"},
    }
    if not is_junction_session(auth):
        current_user = auth["user"]
        role = get_user_role(current_user)
        if role != UserRole.admin:
            query["owner_user_id"] = str(current_user["_id"])

    documents = shops.find(query).sort("created_at", -1)
    return serialize_shops(documents)


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
    return serialize_shops([document])[0]


@router.put("/phone-status", response_model=Shop)
def update_shop_phone_status(
    payload: ShopPhoneStatusUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Shop:
    """Toggle whether the shop mobile number is shown. Body: { "name": "...", "show_phone": true|false }."""
    existing = find_owned_shop_by_name(current_user, payload.name)
    ensure_shop_access(current_user, existing)
    updates: dict = {"show_phone": payload.show_phone, "updated_at": datetime.now(timezone.utc)}
    # Keep catalog phone in sync with the owner profile when the shop doc lacks one.
    if not (isinstance(existing.get("phone_number"), str) and existing["phone_number"].strip()):
        try:
            updates["phone_number"] = get_user_phone_number(current_user)
        except HTTPException:
            pass
    document = shops.find_one_and_update(
        {"_id": existing["_id"]},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    return serialize_shops([document])[0]


@router.get("/{shop_id}", response_model=Shop)
def get_shop(shop_id: str, auth: CatalogReader) -> Shop:
    document = shops.find_one({"_id": parse_object_id(shop_id, "Shop")})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    if not is_junction_session(auth):
        ensure_shop_access(auth["user"], document)
    return serialize_shops([document])[0]


@router.get("/{shop_id}/products", response_model=list[Product])
def list_products_for_shop(shop_id: str, auth: CatalogReader) -> list[Product]:
    """
    List products for one shop.
    Flow for junction.today: /shops/by-location → select shop → /shops/{shop_id}/products.
    """
    document = shops.find_one({"_id": parse_object_id(shop_id, "Shop")})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    if not is_junction_session(auth):
        ensure_shop_access(auth["user"], document)

    store_id = str(document["_id"])
    documents = products.find({"store_id": store_id}).sort("created_at", -1)
    return [serialize_product(item) for item in documents]


@router.post("", response_model=Shop, status_code=status.HTTP_201_CREATED)
def create_shop(payload: ShopCreate, current_user: Annotated[dict, Depends(get_current_user)]) -> Shop:
    """
    Create a shop for the logged-in mobile user.
    One phone/user may own multiple shops; shop names must be unique per owner.
    """
    ensure_shop_indexes()

    city, locality = ensure_city_and_locality(payload.city, payload.locality)
    phone_number = get_user_phone_number(current_user)
    now = datetime.now(timezone.utc)
    document = {
        "name": payload.name,
        "city": city,
        "locality": locality,
        "address": payload.address,
        "open_time": payload.open_time,
        "closed_time": payload.closed_time,
        "is_open": payload.is_open,
        "show_phone": payload.show_phone,
        "phone_number": phone_number,
        "owner_user_id": str(current_user["_id"]),
        "plan": default_plan_document(),
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = shops.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This mobile number already has a shop with this name",
        ) from exc
    document["_id"] = result.inserted_id
    return serialize_shops([document])[0]


@router.get("/{shop_id}/plan", response_model=PlanSummary)
def get_shop_plan(shop_id: str, current_user: Annotated[dict, Depends(get_current_user)]) -> PlanSummary:
    """Return the plan attached to this shop (plan lives on the shop, not the phone)."""
    document = shops.find_one({"_id": parse_object_id(shop_id, "Shop")})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    ensure_shop_access(current_user, document)
    return build_shop_plan_summary(document)


@router.post("/{shop_id}/plan/purchase", response_model=ShopPayment, status_code=status.HTTP_201_CREATED)
def purchase_shop_plan(
    shop_id: str,
    payload: PlanPurchaseRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> ShopPayment:
    """
    Start a paid plan purchase for this shop (pending payment).
    The plan is activated only after POST /payments/{payment_id}/complete.
    Then the shop can add products up to the plan limit.
    """
    document = shops.find_one({"_id": parse_object_id(shop_id, "Shop")})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    ensure_shop_access(current_user, document)
    return create_plan_purchase(current_user, str(document["_id"]), payload.plan_type)


@router.post("/{shop_id}/plan/select", response_model=ShopPayment, status_code=status.HTTP_201_CREATED)
def select_shop_plan(
    shop_id: str,
    payload: ShopPlanSelectRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> ShopPayment:
    """
    Alias of POST /shops/{shop_id}/plan/purchase for paid plans.
    Creates a pending payment; call POST /payments/{id}/complete to activate the plan,
    then add products under that shop's allowance.
    Admins activate immediately (payment recorded as paid).
    """
    document = shops.find_one({"_id": parse_object_id(shop_id, "Shop")})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    ensure_shop_access(current_user, document)
    store_id = str(document["_id"])

    if get_user_role(current_user) == UserRole.admin:
        select_plan_for_shop(store_id, payload.plan_type)
        from .database import shop_payments as payments_col
        from .plan_service import PLAN_CATALOG, utc_now
        from .shop_payments import PaymentKind, ShopPaymentStatus, serialize_payment

        details = PLAN_CATALOG[payload.plan_type.value]
        now = utc_now()
        payment_doc = {
            "store_id": store_id,
            "owner_user_id": document["owner_user_id"],
            "kind": PaymentKind.plan.value,
            "status": ShopPaymentStatus.paid.value,
            "amount_inr": int(details["price_inr"]),
            "currency": "INR",
            "plan_type": payload.plan_type.value,
            "packs": None,
            "slots": None,
            "description": f"{details['name']} plan (admin activation)",
            "payment_method": None,
            "payment_reference": "admin_bypass",
            "created_at": now,
            "updated_at": now,
            "paid_at": now,
            "fulfilled_at": now,
        }
        result = payments_col.insert_one(payment_doc)
        payment_doc["_id"] = result.inserted_id
        return serialize_payment(payment_doc)

    return create_plan_purchase(current_user, store_id, payload.plan_type)


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
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This mobile number already has a shop with this name",
        ) from exc
    return serialize_shops([document])[0]


@router.delete("/{shop_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shop(shop_id: str, current_user: Annotated[dict, Depends(get_current_user)]) -> Response:
    existing = shops.find_one({"_id": parse_object_id(shop_id, "Shop")})
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    ensure_shop_access(current_user, existing)
    delete_shop_cascade(shop_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
