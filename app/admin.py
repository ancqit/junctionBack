from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from pymongo import ReturnDocument

from .admin_registry import (
    ADMIN_LIST_PATH,
    get_admin_registry_loaded_at,
    load_admin_registry,
    refresh_admin_registry,
)
from .login import get_current_user
from .plan_service import (
    PlanSummary,
    PlanStatus,
    PlanType,
    admin_activate_user_plan,
    admin_deactivate_user_plan,
    admin_delete_users,
    admin_reactivate_user_plan,
    build_plan_summary,
)
from .role_keeper import get_role_keeper_document, load_role_keeper, save_role_keeper
from .roles import UserRole, get_user_role
from .plan_applications import PlanApplication, serialize_application
from .database import plan_applications, users
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


class ViewerRecord(BaseModel):
    id: str
    display_name: str
    email: EmailStr | None = None
    phone_number: str | None = None
    account_status: str
    plan_type: PlanType
    plan_status: PlanStatus
    days_remaining: int | None = None
    created_at: datetime
    updated_at: datetime


class BulkDeleteUsersRequest(BaseModel):
    user_ids: list[str]


class BulkDeleteUsersResponse(BaseModel):
    deleted_count: int
    deleted_ids: list[str]
    not_found_ids: list[str]
    protected_owner_ids: list[str]
    protected_admin_ids: list[str]


class UpdateUserRoleRequest(BaseModel):
    role: UserRole


class RoleKeeperResponse(BaseModel):
    mappings: dict[str, UserRole]
    updated_at: datetime


class RoleKeeperUpdateRequest(BaseModel):
    mappings: dict[str, UserRole]


class AdminRegistryResponse(BaseModel):
    mappings: dict[str, str]
    loaded_at: datetime | None
    file_path: str


class ReactivateUserResponse(BaseModel):
    user: AdminUserRecord
    restored_role: UserRole
    restored_plan: PlanSummary
    restored_activities: list[str]


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


def serialize_viewer(user: dict) -> ViewerRecord:
    plan = build_plan_summary(user)
    return ViewerRecord(
        id=str(user["_id"]),
        display_name=user.get("display_name", ""),
        email=user.get("email"),
        phone_number=user.get("phone_number"),
        account_status=user.get("account_status", "active"),
        plan_type=plan.type,
        plan_status=plan.status,
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


@router.post("/users/{user_id}/reactivate", response_model=ReactivateUserResponse)
def reactivate_user(user_id: str, _: Annotated[dict, Depends(require_admin)]) -> ReactivateUserResponse:
    object_id = parse_object_id(user_id, "User")
    result = admin_reactivate_user_plan(object_id)
    return ReactivateUserResponse(
        user=serialize_admin_user(result["user"]),
        restored_role=result["restored_role"],
        restored_plan=result["restored_plan"],
        restored_activities=result["restored_activities"],
    )


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


@router.get("/viewers", response_model=list[ViewerRecord])
def list_viewers(_: Annotated[dict, Depends(require_admin)]) -> list[ViewerRecord]:
    documents = users.find({"role": UserRole.viewer.value}).sort("created_at", -1)
    return [serialize_viewer(document) for document in documents]


@router.delete("/users", response_model=BulkDeleteUsersResponse)
def delete_users(
    payload: BulkDeleteUsersRequest,
    _: Annotated[dict, Depends(require_admin)],
) -> BulkDeleteUsersResponse:
    """Delete viewer accounts only. Shop owners and admins can never be deleted."""
    if not payload.user_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide at least one user_id")
    object_ids = [parse_object_id(user_id, "User") for user_id in payload.user_ids]
    result = admin_delete_users(object_ids)
    return BulkDeleteUsersResponse(**result)


@router.delete("/viewers", response_model=BulkDeleteUsersResponse)
def delete_viewers(
    payload: BulkDeleteUsersRequest,
    _: Annotated[dict, Depends(require_admin)],
) -> BulkDeleteUsersResponse:
    """Alias for DELETE /admin/users — bulk-delete viewer accounts only."""
    return delete_users(payload, _)


@router.get("/plan-applications", response_model=list[PlanApplication])
def list_plan_applications(_: Annotated[dict, Depends(require_admin)]) -> list[PlanApplication]:
    documents = plan_applications.find().sort("created_at", -1)
    return [serialize_application(document) for document in documents]


@router.get("/waitlist", response_model=list[PlanApplication])
def list_waitlist(_: Annotated[dict, Depends(require_admin)]) -> list[PlanApplication]:
    """Alias for GET /admin/plan-applications."""
    return list_plan_applications(_)


@router.get("/role-keeper", response_model=RoleKeeperResponse)
def get_role_keeper(_: Annotated[dict, Depends(require_admin)]) -> RoleKeeperResponse:
    document = get_role_keeper_document()
    mappings = load_role_keeper()
    return RoleKeeperResponse(
        mappings={key: UserRole(value) for key, value in mappings.items()},
        updated_at=document["updated_at"],
    )


@router.put("/role-keeper", response_model=RoleKeeperResponse)
def update_role_keeper(
    payload: RoleKeeperUpdateRequest,
    _: Annotated[dict, Depends(require_admin)],
) -> RoleKeeperResponse:
    try:
        role_values = {key: value.value for key, value in payload.mappings.items()}
        if UserRole.admin.value in role_values.values():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Admin roles are managed via admin.json, not role keeper",
            )
        mappings = save_role_keeper(role_values)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    document = get_role_keeper_document()
    return RoleKeeperResponse(
        mappings={key: UserRole(value) for key, value in mappings.items()},
        updated_at=document["updated_at"],
    )


@router.get("/admins", response_model=AdminRegistryResponse)
def get_admin_registry(_: Annotated[dict, Depends(require_admin)]) -> AdminRegistryResponse:
    mappings = load_admin_registry()
    return AdminRegistryResponse(
        mappings=mappings,
        loaded_at=get_admin_registry_loaded_at(),
        file_path=ADMIN_LIST_PATH,
    )


@router.post("/admins/refresh", response_model=AdminRegistryResponse)
def refresh_admin_registry_endpoint(_: Annotated[dict, Depends(require_admin)]) -> AdminRegistryResponse:
    mappings = refresh_admin_registry()
    return AdminRegistryResponse(
        mappings=mappings,
        loaded_at=get_admin_registry_loaded_at(),
        file_path=ADMIN_LIST_PATH,
    )
