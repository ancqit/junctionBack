from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .database import plan_applications, shops
from .login import get_current_user
from .plan_service import PLAN_CATALOG, PlanType, build_plan_summary
from .roles import UserRole, get_user_role
from .utils import parse_object_id

router = APIRouter(prefix="/plans", tags=["plans"])


class ApplicationStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ApplicationLocation(BaseModel):
    city: str
    locality: str


class ApplicantIdentity(BaseModel):
    display_name: str
    phone_number: str | None = None
    email: EmailStr | None = None


class PlanApplyPreview(BaseModel):
    requested_plan_type: PlanType
    requested_plan_name: str
    current_plan_type: PlanType
    current_plan_name: str
    is_plan_switch: bool
    message: str


class PlanApplyRequest(BaseModel):
    plan_type: PlanType
    shop_id: str = Field(min_length=1, max_length=80)


class PlanApplication(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    user_id: str
    shop_id: str
    shop_name: str
    identity: ApplicantIdentity
    location: ApplicationLocation
    requested_plan_type: PlanType
    current_plan_type: PlanType
    is_plan_switch: bool
    switch_message: str
    status: ApplicationStatus
    created_at: datetime
    updated_at: datetime


def plan_display_name(plan_type: PlanType) -> str:
    return PLAN_CATALOG[plan_type.value]["name"]


def build_switch_message(current: PlanType, requested: PlanType) -> tuple[bool, str]:
    is_switch = current != requested
    if not is_switch:
        return False, f"You are applying for the {plan_display_name(requested)} plan."
    return True, f"You are switching plans from {plan_display_name(current)} to {plan_display_name(requested)}."


def get_user_shop(shop_id: str, user: dict) -> dict:
    shop = shops.find_one({"_id": parse_object_id(shop_id, "Shop")})
    if shop is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    role = get_user_role(user)
    if role != UserRole.admin and str(shop.get("owner_user_id")) != str(user["_id"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this shop")
    return shop


def serialize_application(document: dict) -> PlanApplication:
    return PlanApplication(
        id=str(document["_id"]),
        user_id=document["user_id"],
        shop_id=document["shop_id"],
        shop_name=document["shop_name"],
        identity=ApplicantIdentity(**document["identity"]),
        location=ApplicationLocation(**document["location"]),
        requested_plan_type=PlanType(document["requested_plan_type"]),
        current_plan_type=PlanType(document["current_plan_type"]),
        is_plan_switch=document["is_plan_switch"],
        switch_message=document["switch_message"],
        status=ApplicationStatus(document["status"]),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


def _require_viewer_for_waitlist(user: dict) -> None:
    if get_user_role(user) == UserRole.admin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Admins are not subject to plan applications")
    if get_user_role(user) != UserRole.viewer:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only viewers can join the waitlist. Owners can select a plan directly at POST /plans/select.",
        )


@router.get("/apply/preview", response_model=PlanApplyPreview)
def preview_plan_application(
    current_user: Annotated[dict, Depends(get_current_user)],
    plan_type: PlanType = Query(..., description="Plan the user wants to apply for"),
) -> PlanApplyPreview:
    if plan_type == PlanType.free_trial:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Free trial cannot be applied for")

    _require_viewer_for_waitlist(current_user)
    summary = build_plan_summary(current_user)
    current_type = summary.type
    is_switch, message = build_switch_message(current_type, plan_type)
    return PlanApplyPreview(
        requested_plan_type=plan_type,
        requested_plan_name=plan_display_name(plan_type),
        current_plan_type=current_type,
        current_plan_name=plan_display_name(current_type),
        is_plan_switch=is_switch,
        message=message,
    )


@router.post("/apply", response_model=PlanApplication, status_code=status.HTTP_201_CREATED)
def apply_for_plan(
    payload: PlanApplyRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> PlanApplication:
    if payload.plan_type == PlanType.free_trial:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Free trial cannot be applied for")
    _require_viewer_for_waitlist(current_user)

    shop = get_user_shop(payload.shop_id, current_user)
    city = shop.get("city", "").strip()
    locality = shop.get("locality", "").strip()
    if not city or not locality:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Shop must have city and locality before applying for a plan",
        )

    summary = build_plan_summary(current_user)
    current_type = summary.type
    is_switch, message = build_switch_message(current_type, payload.plan_type)

    now = datetime.now(timezone.utc)
    document = {
        "user_id": str(current_user["_id"]),
        "shop_id": str(shop["_id"]),
        "shop_name": shop["name"],
        "identity": {
            "display_name": current_user.get("display_name", ""),
            "phone_number": current_user.get("phone_number"),
            "email": current_user.get("email"),
        },
        "location": {"city": city, "locality": locality},
        "requested_plan_type": payload.plan_type.value,
        "current_plan_type": current_type.value,
        "is_plan_switch": is_switch,
        "switch_message": message,
        "status": ApplicationStatus.pending.value,
        "created_at": now,
        "updated_at": now,
    }

    plan_applications.create_index([("user_id", 1), ("status", 1)])
    plan_applications.create_index("created_at")

    existing = plan_applications.find_one(
        {"user_id": str(current_user["_id"]), "status": ApplicationStatus.pending.value},
    )
    if existing is not None:
        updated = plan_applications.find_one_and_update(
            {"_id": existing["_id"]},
            {"$set": {**document, "created_at": existing["created_at"]}},
            return_document=ReturnDocument.AFTER,
        )
        return serialize_application(updated)

    try:
        result = plan_applications.insert_one(document)
    except DuplicateKeyError:
        updated = plan_applications.find_one_and_update(
            {"user_id": str(current_user["_id"]), "status": ApplicationStatus.pending.value},
            {"$set": document},
            return_document=ReturnDocument.AFTER,
        )
        return serialize_application(updated)
    document["_id"] = result.inserted_id
    return serialize_application(document)


@router.get("/applications/me", response_model=PlanApplication | None)
def get_my_plan_application(current_user: Annotated[dict, Depends(get_current_user)]) -> PlanApplication | None:
    document = plan_applications.find_one(
        {"user_id": str(current_user["_id"]), "status": ApplicationStatus.pending.value},
        sort=[("created_at", -1)],
    )
    if document is None:
        return None
    return serialize_application(document)
