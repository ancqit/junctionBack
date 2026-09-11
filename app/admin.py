from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from pymongo import ReturnDocument

from .admin_registry import (
    ADMIN_LIST_PATH,
    get_admin_registry_loaded_at,
    load_admin_registry,
    refresh_admin_registry,
)
from .database import plan_applications, shops, users
from .login import get_current_user
from .plan_applications import PlanApplication, serialize_application
from .plan_service import (
    PlanStatus,
    PlanType,
    admin_activate_viewer_from_waitlist,
    admin_delete_users,
    build_plan_summary,
    select_plan_for_shop,
    select_plan_for_user,
)
from .role_keeper import get_role_keeper_document, load_role_keeper, save_role_keeper
from .roles import UserRole, get_user_role
from .utils import parse_object_id

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(current_user: Annotated[dict, Depends(get_current_user)]) -> dict:
    if get_user_role(current_user) != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


def _normalize_phone_query(value: str) -> str:
    digits = "".join(ch for ch in value.strip() if ch.isdigit())
    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    if value.strip().startswith("+") and digits:
        return f"+{digits}"
    return value.strip()


class AdminShopBrief(BaseModel):
    id: str
    name: str
    plan_type: PlanType | None = None
    plan_status: PlanStatus | None = None
    plan_name: str | None = None


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
    shop_count: int = 0
    shops: list[AdminShopBrief] = Field(default_factory=list)
    can_create_shop: bool = False
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


class AdminSetUserPlanRequest(BaseModel):
    plan_type: PlanType
    sync_shops: bool = True


class AdminRejectApplicationRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=240)


class RoleKeeperResponse(BaseModel):
    mappings: dict[str, UserRole]
    updated_at: datetime


class RoleKeeperUpdateRequest(BaseModel):
    mappings: dict[str, UserRole]


class AdminRegistryResponse(BaseModel):
    mappings: dict[str, str]
    loaded_at: datetime | None
    file_path: str


def _shop_briefs_by_owner(owner_ids: set[str]) -> dict[str, list[AdminShopBrief]]:
    if not owner_ids:
        return {}
    result: dict[str, list[AdminShopBrief]] = {owner_id: [] for owner_id in owner_ids}
    for document in shops.find({"owner_user_id": {"$in": list(owner_ids)}}).sort("created_at", -1):
        owner_id = str(document.get("owner_user_id") or "")
        plan = document.get("plan") or {}
        plan_type_raw = plan.get("type")
        plan_status_raw = plan.get("status")
        try:
            plan_type = PlanType(plan_type_raw) if plan_type_raw else None
        except ValueError:
            plan_type = None
        try:
            plan_status = PlanStatus(plan_status_raw) if plan_status_raw else None
        except ValueError:
            plan_status = None
        brief = AdminShopBrief(
            id=str(document["_id"]),
            name=str(document.get("name") or "Shop"),
            plan_type=plan_type,
            plan_status=plan_status,
            plan_name=str(plan.get("name") or (plan_type.value if plan_type else "") or None) or None,
        )
        result.setdefault(owner_id, []).append(brief)
    return result


def serialize_admin_user(user: dict, shop_briefs: list[AdminShopBrief] | None = None) -> AdminUserRecord:
    plan = build_plan_summary(user)
    phone = user.get("phone_number")
    has_phone = bool(isinstance(phone, str) and phone.strip())
    briefs = shop_briefs if shop_briefs is not None else []
    return AdminUserRecord(
        id=str(user["_id"]),
        display_name=user.get("display_name", ""),
        email=user.get("email"),
        phone_number=phone,
        role=get_user_role(user),
        account_status=user.get("account_status", "active"),
        plan_type=plan.type,
        plan_status=plan.status,
        plan_is_active=plan.is_active,
        plan_name=plan.name,
        selected_plan_type=plan.selected_plan_type,
        in_grace_period=plan.in_grace_period,
        days_remaining=plan.days_remaining,
        shop_count=len(briefs),
        shops=briefs,
        can_create_shop=has_phone,
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
def list_users(
    _: Annotated[dict, Depends(require_admin)],
    q: str | None = Query(default=None, description="Search phone, name, or email"),
    role: UserRole | None = Query(default=None),
    has_shop: bool | None = Query(default=None, description="Filter users with/without shops"),
    has_phone: bool | None = Query(default=None, description="Filter users with/without phone"),
) -> list[AdminUserRecord]:
    """List users with phone-first fields and shop counts (numbers are primary)."""
    documents = list(users.find().sort("created_at", -1))
    owner_ids = {str(document["_id"]) for document in documents}
    shops_by_owner = _shop_briefs_by_owner(owner_ids)
    rows = [
        serialize_admin_user(document, shops_by_owner.get(str(document["_id"]), []))
        for document in documents
    ]

    needle = (q or "").strip().lower()
    phone_needle = _normalize_phone_query(q) if q else ""
    filtered: list[AdminUserRecord] = []
    for row in rows:
        if role is not None and row.role != role:
            continue
        if has_shop is True and row.shop_count <= 0:
            continue
        if has_shop is False and row.shop_count > 0:
            continue
        if has_phone is True and not row.phone_number:
            continue
        if has_phone is False and row.phone_number:
            continue
        if needle:
            haystack = " ".join(
                [
                    row.display_name or "",
                    row.email or "",
                    row.phone_number or "",
                    row.id,
                    " ".join(shop.name for shop in row.shops),
                ]
            ).lower()
            phone_match = bool(phone_needle and row.phone_number and phone_needle in row.phone_number)
            if needle not in haystack and not phone_match:
                continue
        filtered.append(row)
    return filtered


@router.get("/users/by-phone", response_model=AdminUserRecord)
def get_user_by_phone(
    phone: str = Query(..., min_length=8, max_length=20),
    _: dict = Depends(require_admin),
) -> AdminUserRecord:
    """Primary ops lookup: find a user by phone number."""
    normalized = _normalize_phone_query(phone)
    user = users.find_one({"phone_number": normalized})
    if user is None and normalized != phone.strip():
        user = users.find_one({"phone_number": phone.strip()})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found for that phone")
    briefs = _shop_briefs_by_owner({str(user["_id"])}).get(str(user["_id"]), [])
    return serialize_admin_user(user, briefs)


@router.post("/users/{user_id}/activate", response_model=AdminUserRecord)
def activate_user(user_id: str, _: Annotated[dict, Depends(require_admin)]) -> AdminUserRecord:
    """Approve a viewer's pending waitlist application and upgrade them to owner."""
    object_id = parse_object_id(user_id, "User")
    admin_activate_viewer_from_waitlist(object_id)
    user = users.find_one({"_id": object_id})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    briefs = _shop_briefs_by_owner({user_id}).get(user_id, [])
    return serialize_admin_user(user, briefs)


@router.patch("/users/{user_id}/role", response_model=AdminUserRecord)
def update_user_role(
    user_id: str,
    payload: UpdateUserRoleRequest,
    current_admin: Annotated[dict, Depends(require_admin)],
) -> AdminUserRecord:
    object_id = parse_object_id(user_id, "User")
    if str(current_admin["_id"]) == user_id and payload.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot remove your own admin role")

    updates: dict = {
        "role": payload.role.value,
        "updated_at": datetime.now(timezone.utc),
    }
    unset: dict = {}
    if payload.role == UserRole.viewer:
        updates["plan.viewing_applied"] = True
    elif payload.role == UserRole.owner:
        updates["plan.viewing_applied"] = False
    elif payload.role == UserRole.admin:
        unset["plan.viewing_applied"] = ""

    mongo_update: dict = {"$set": updates}
    if unset:
        mongo_update["$unset"] = unset

    user = users.find_one_and_update(
        {"_id": object_id},
        mongo_update,
        return_document=ReturnDocument.AFTER,
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    briefs = _shop_briefs_by_owner({user_id}).get(user_id, [])
    return serialize_admin_user(user, briefs)


@router.patch("/users/{user_id}/plan", response_model=AdminUserRecord)
def set_user_plan(
    user_id: str,
    payload: AdminSetUserPlanRequest,
    _: Annotated[dict, Depends(require_admin)],
) -> AdminUserRecord:
    """Admin plan grip: set the user plan and optionally sync owned shops."""
    object_id = parse_object_id(user_id, "User")
    user = users.find_one({"_id": object_id})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if get_user_role(user) == UserRole.admin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Admin accounts do not use plans")

    if payload.plan_type == PlanType.free_trial:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Assign a paid plan type")

    select_plan_for_user(object_id, payload.plan_type)
    users.update_one(
        {"_id": object_id},
        {
            "$set": {
                "role": UserRole.owner.value,
                "plan.viewing_applied": False,
                "plan.activated_by": "admin",
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )

    if payload.sync_shops:
        owned = shops.find({"owner_user_id": user_id})
        for shop in owned:
            select_plan_for_shop(str(shop["_id"]), payload.plan_type)

    updated = users.find_one({"_id": object_id})
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    briefs = _shop_briefs_by_owner({user_id}).get(user_id, [])
    return serialize_admin_user(updated, briefs)


@router.post("/plan-applications/{application_id}/reject", response_model=PlanApplication)
def reject_plan_application(
    application_id: str,
    payload: AdminRejectApplicationRequest,
    _: Annotated[dict, Depends(require_admin)],
) -> PlanApplication:
    object_id = parse_object_id(application_id, "Plan application")
    now = datetime.now(timezone.utc)
    updates = {
        "status": "rejected",
        "rejected_at": now,
        "updated_at": now,
    }
    if payload.reason and payload.reason.strip():
        updates["reject_reason"] = payload.reason.strip()
    document = plan_applications.find_one_and_update(
        {"_id": object_id, "status": "pending"},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        existing = plan_applications.find_one({"_id": object_id})
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only pending applications can be rejected",
        )
    return serialize_application(document)


@router.get("/viewers", response_model=list[ViewerRecord])
def list_viewers(_: Annotated[dict, Depends(require_admin)]) -> list[ViewerRecord]:
    # FIFO queue: oldest first
    documents = users.find({"role": UserRole.viewer.value}).sort("created_at", 1)
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
    # FIFO queue: oldest first
    documents = plan_applications.find().sort("created_at", 1)
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
