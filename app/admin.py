from datetime import datetime, timezone
from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from pymongo import ReturnDocument

from .login import get_current_user
from .plan_service import PlanSummary, PlanStatus, PlanType, admin_activate_user_plan, admin_deactivate_user_plan, build_plan_summary
from .roles import UserRole, get_user_role
from .database import users
from .utils import parse_object_id

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(current_user: Annotated[dict, Depends(get_current_user)]) -> dict:
    if get_user_role(current_user) != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


class AdminUserRecord(BaseModel):
    id: str
    display_name: str
    email: EmailStr | None = None
    phone_number: str | None = None
    role: UserRole
    account_status: str
    plan_type: PlanType
    plan_status: PlanStatus
    plan_is_active: bool
    plan_name: str
    selected_plan_type: PlanType | None = None
    in_grace_period: bool = False
    days_remaining: int | None = None
    created_at: datetime
    updated_at: datetime


class UpdateUserRoleRequest(BaseModel):
    role: UserRole


def serialize_admin_user(user: dict) -> AdminUserRecord:
    plan = build_plan_summary(user)
    return AdminUserRecord(
        id=str(user["_id"]),
        display_name=user.get("display_name", ""),
        email=user.get("email"),
        phone_number=user.get("phone_number"),
        role=get_user_role(user),
        account_status=user.get("account_status", "active"),
        plan_type=plan.type,
        plan_status=plan.status,
        plan_is_active=plan.is_active,
        plan_name=plan.name,
        selected_plan_type=plan.selected_plan_type,
        in_grace_period=plan.in_grace_period,
        days_remaining=plan.days_remaining,
        created_at=user["created_at"],
        updated_at=user["updated_at"],
    )


@router.get("/users", response_model=list[AdminUserRecord])
def list_users(_: Annotated[dict, Depends(require_admin)]) -> list[AdminUserRecord]:
    documents = users.find().sort("created_at", -1)
    return [serialize_admin_user(document) for document in documents]


@router.post("/users/{user_id}/activate", response_model=AdminUserRecord)
def activate_user(user_id: str, _: Annotated[dict, Depends(require_admin)]) -> AdminUserRecord:
    object_id = parse_object_id(user_id, "User")
    admin_activate_user_plan(object_id)
    user = users.find_one({"_id": object_id})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return serialize_admin_user(user)


@router.post("/users/{user_id}/deactivate", response_model=AdminUserRecord)
def deactivate_user(user_id: str, _: Annotated[dict, Depends(require_admin)]) -> AdminUserRecord:
    object_id = parse_object_id(user_id, "User")
    admin_deactivate_user_plan(object_id)
    user = users.find_one({"_id": object_id})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return serialize_admin_user(user)


@router.patch("/users/{user_id}/role", response_model=AdminUserRecord)
def update_user_role(
    user_id: str,
    payload: UpdateUserRoleRequest,
    current_admin: Annotated[dict, Depends(require_admin)],
) -> AdminUserRecord:
    object_id = parse_object_id(user_id, "User")
    if str(current_admin["_id"]) == user_id and payload.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot remove your own admin role")

    user = users.find_one_and_update(
        {"_id": object_id},
        {"$set": {"role": payload.role.value, "updated_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return serialize_admin_user(user)
