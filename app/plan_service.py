import os
from datetime import datetime, timedelta, timezone
from enum import Enum

from bson import ObjectId
from fastapi import HTTPException, status
from pydantic import BaseModel
from pymongo import ReturnDocument

from .database import plan_applications, products, shops, users
from .roles import UserRole, get_user_role
from .utils import parse_object_id

TRIAL_DAYS = int(os.getenv("PLAN_TRIAL_DAYS", "15"))
GRACE_DAYS = int(os.getenv("PLAN_GRACE_DAYS", "15"))
PLAN_YEAR_DAYS = int(os.getenv("PLAN_YEAR_DAYS", os.getenv("PLAN_STARTER_DAYS", "365")))


class PlanType(str, Enum):
    free_trial = "free_trial"
    starter = "starter"
    serious = "serious"
    growth = "growth"
    premium = "premium"


class PlanStatus(str, Enum):
    active = "active"
    grace_period = "grace_period"
    expired = "expired"
    cancelled = "cancelled"
    deactivated = "deactivated"


# Ascending by price_inr (insertion order drives GET /plans).
PLAN_CATALOG: dict[str, dict] = {
    PlanType.free_trial.value: {
        "name": "Free Trial",
        "price_inr": 0,
        "max_products": 40,
        "profile_only": False,
        "description": "Shop profile with up to 40 products for 15 days",
        "duration_days": TRIAL_DAYS,
    },
    PlanType.starter.value: {
        "name": "Starter",
        "price_inr": 999,
        "max_products": 40,
        "profile_only": False,
        "description": "Shop profile with up to 40 products for 1 year (INR 999)",
        "duration_days": PLAN_YEAR_DAYS,
    },
    PlanType.serious.value: {
        "name": "Serious",
        "price_inr": 1999,
        "max_products": 40,
        "profile_only": False,
        "description": "Direct conversion from Free Trial — keep up to 40 products for 1 year (INR 1999)",
        "duration_days": PLAN_YEAR_DAYS,
    },
    PlanType.growth.value: {
        "name": "Growth",
        "price_inr": 2999,
        "max_products": 80,
        "profile_only": False,
        "description": "Shop profile with up to 80 products for 1 year (INR 2999)",
        "duration_days": PLAN_YEAR_DAYS,
    },
    PlanType.premium.value: {
        "name": "Premium",
        "price_inr": 5999,
        "max_products": 150,
        "profile_only": False,
        "description": "Shop profile with up to 150 products for 1 year (INR 5999)",
        "duration_days": PLAN_YEAR_DAYS,
    },
}


class PlanSummary(BaseModel):
    type: PlanType
    status: PlanStatus
    name: str
    price_inr: int
    max_products: int | None
    profile_only: bool
    description: str
    started_at: datetime
    ends_at: datetime | None = None
    days_remaining: int | None = None
    is_active: bool
    trial_used: bool
    selected_plan_type: PlanType | None = None
    in_grace_period: bool = False
    grace_ends_at: datetime | None = None


class PlanOption(BaseModel):
    type: PlanType
    name: str
    price_inr: int
    max_products: int | None
    profile_only: bool
    description: str
    duration_days: int | None = None


def plan_option(plan_type: PlanType) -> PlanOption:
    details = PLAN_CATALOG[plan_type.value]
    return PlanOption(type=plan_type, **details)


def list_plan_options() -> list[PlanOption]:
    return [plan_option(PlanType(plan_type)) for plan_type in PLAN_CATALOG if plan_type != PlanType.free_trial.value]


def list_all_plans() -> list[PlanOption]:
    return [plan_option(PlanType(plan_type)) for plan_type in PLAN_CATALOG]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def admin_plan_summary() -> PlanSummary:
    now = utc_now()
    return PlanSummary(
        type=PlanType.premium,
        status=PlanStatus.active,
        name="Admin",
        price_inr=0,
        max_products=None,
        profile_only=False,
        description="Administrator access — not subject to plan limits",
        started_at=now,
        ends_at=None,
        days_remaining=None,
        is_active=True,
        trial_used=False,
        selected_plan_type=PlanType.premium,
        in_grace_period=False,
        grace_ends_at=None,
    )


def admin_shop_plan_document() -> dict:
    """Persistent shop plan for admin-owned shops — premium, no expiry, unlocked."""
    now = utc_now()
    return {
        "type": PlanType.premium.value,
        "status": PlanStatus.active.value,
        "started_at": now,
        "ends_at": None,
        "trial_used": False,
        "viewing_applied": False,
        "selected_plan_type": PlanType.premium.value,
        "activated_by": "admin_default",
    }


def _shop_owner(shop: dict) -> dict | None:
    owner_raw = str(shop.get("owner_user_id") or "").strip()
    if not owner_raw or not ObjectId.is_valid(owner_raw):
        return None
    return users.find_one({"_id": ObjectId(owner_raw)})


def shop_owner_is_admin(shop: dict) -> bool:
    owner = _shop_owner(shop)
    return owner is not None and get_user_role(owner) == UserRole.admin


def default_plan_document() -> dict:
    now = utc_now()
    return {
        "type": PlanType.free_trial.value,
        "status": PlanStatus.active.value,
        "started_at": now,
        "ends_at": now + timedelta(days=TRIAL_DAYS),
        "trial_used": True,
        "selected_plan_type": None,
    }


def viewer_shop_plan_document() -> dict:
    """Shop created after the number's trial — profile-only / expired (viewer mode)."""
    now = utc_now()
    return {
        "type": PlanType.free_trial.value,
        "status": PlanStatus.expired.value,
        "started_at": now,
        "ends_at": now,
        "trial_used": True,
        "viewing_applied": True,
        "selected_plan_type": None,
        "activated_by": None,
    }


def user_has_active_number_trial(user: dict) -> bool:
    """Trial is attached to the phone/user — one trial window for the number."""
    if get_user_role(user) == UserRole.admin:
        return False
    plan = user.get("plan") or {}
    if plan.get("viewing_applied"):
        return False
    if plan.get("type") != PlanType.free_trial.value:
        return False
    if plan.get("status") != PlanStatus.active.value:
        return False
    ends_at = plan.get("ends_at")
    if ends_at is None:
        return True
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)
    return utc_now() < ends_at


def plan_document_for_new_shop(owner: dict) -> tuple[dict, bool, str | None]:
    """
    New shop plan rules (anti-outsmart):
    - While the number's free trial is active → shop shares that trial window.
    - After trial ends → shop is viewer mode (locked, expired plan) even if another
      owned shop already has a paid plan.
    """
    if get_user_role(owner) == UserRole.admin:
        return admin_shop_plan_document(), False, None

    if user_has_active_number_trial(owner):
        plan = owner.get("plan") or {}
        doc = default_plan_document()
        if plan.get("ends_at") is not None:
            doc["ends_at"] = plan["ends_at"]
        if plan.get("started_at") is not None:
            doc["started_at"] = plan["started_at"]
        return doc, False, None

    return viewer_shop_plan_document(), True, "plan_expired"


def lock_non_active_shops_for_owner(owner_user_id: str) -> None:
    """Lock owned shops that are not on an active/grace paid plan (viewer mode)."""
    now = utc_now()
    for shop in shops.find({"owner_user_id": str(owner_user_id)}):
        plan = shop.get("plan") or {}
        status_value = plan.get("status")
        if is_paid_plan(plan.get("type")) and status_value in {
            PlanStatus.active.value,
            PlanStatus.grace_period.value,
        }:
            continue
        shops.update_one(
            {"_id": shop["_id"]},
            {
                "$set": {
                    "is_locked": True,
                    "lock_reason": "plan_expired",
                    "plan.status": PlanStatus.deactivated.value,
                    "plan.viewing_applied": True,
                    "plan.trial_used": True,
                    "updated_at": now,
                }
            },
        )


def is_paid_plan(plan_type: str | None) -> bool:
    return plan_type in {
        PlanType.serious.value,
        PlanType.starter.value,
        PlanType.growth.value,
        PlanType.premium.value,
    }


def initialize_user_plan(user_id: ObjectId) -> dict:
    plan = default_plan_document()
    users.update_one(
        {"_id": user_id, "plan": {"$exists": False}},
        {"$set": {"plan": plan, "updated_at": utc_now()}},
    )
    user = users.find_one({"_id": user_id})
    return user.get("plan", plan) if user else plan


def restore_persisted_plan(user: dict) -> dict:
    """Reload plan from DB, restore a selected paid plan on login, and apply expiry checks."""
    if get_user_role(user) == UserRole.admin:
        return user

    refreshed = users.find_one({"_id": user["_id"]})
    if refreshed is None:
        return user

    plan = refreshed.get("plan")
    if plan is None:
        initialize_user_plan(refreshed["_id"])
        refreshed = users.find_one({"_id": user["_id"]}) or refreshed
        refreshed = expire_trial_if_needed(refreshed)
        refreshed = expire_paid_plan_if_needed(refreshed)
        return expire_grace_period_if_needed(refreshed)

    if plan.get("status") not in {
        PlanStatus.grace_period.value,
        PlanStatus.expired.value,
    } and not plan.get("viewing_applied"):
        selected_plan_type = plan.get("selected_plan_type")
        if selected_plan_type is None and is_paid_plan(plan.get("type")):
            selected_plan_type = plan.get("type")

        if is_paid_plan(selected_plan_type):
            now = utc_now()
            updated = users.find_one_and_update(
                {"_id": refreshed["_id"]},
                {
                    "$set": {
                        "plan.type": selected_plan_type,
                        "plan.selected_plan_type": selected_plan_type,
                        "plan.status": PlanStatus.active.value,
                        "plan.ends_at": None,
                        "plan.restored_at": now,
                        "updated_at": now,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
            refreshed = updated or refreshed

    refreshed = expire_trial_if_needed(refreshed)
    refreshed = expire_paid_plan_if_needed(refreshed)
    return expire_grace_period_if_needed(refreshed)


def downgrade_owner_to_viewer(user: dict) -> dict:
    """Move an owner to viewer after trial or grace period ends (deactivated → viewer)."""
    now = utc_now()
    updated = users.find_one_and_update(
        {"_id": user["_id"]},
        {
            "$set": {
                "role": UserRole.viewer.value,
                "plan.status": PlanStatus.deactivated.value,
                "plan.viewing_applied": True,
                "plan.downgraded_at": now,
                "plan.closed_at": now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    result = updated or user
    # Number trial over → shops without their own paid plan go to viewer mode.
    lock_non_active_shops_for_owner(str(result["_id"]))
    return result


def expire_paid_plan_if_needed(user: dict) -> dict:
    """When a paid plan (Starter/Growth/Premium) expires, start a 15-day grace period."""
    plan = user.get("plan")
    if not plan or plan.get("status") != PlanStatus.active.value:
        return user
    if not is_paid_plan(plan.get("type")):
        return user

    ends_at = plan.get("ends_at")
    if ends_at is None:
        return user

    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)

    if utc_now() < ends_at:
        return user

    now = utc_now()
    grace_ends_at = now + timedelta(days=GRACE_DAYS)
    updated = users.find_one_and_update(
        {"_id": user["_id"], "plan.status": PlanStatus.active.value},
        {
            "$set": {
                "plan.status": PlanStatus.grace_period.value,
                "plan.selected_plan_type": plan.get("selected_plan_type") or plan.get("type"),
                "plan.grace_started_at": now,
                "plan.grace_ends_at": grace_ends_at,
                "plan.expired_at": now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    return updated or user


def expire_grace_period_if_needed(user: dict) -> dict:
    plan = user.get("plan")
    if not plan or plan.get("status") != PlanStatus.grace_period.value:
        return user

    grace_ends_at = plan.get("grace_ends_at")
    if grace_ends_at is None:
        return user

    if grace_ends_at.tzinfo is None:
        grace_ends_at = grace_ends_at.replace(tzinfo=timezone.utc)

    if utc_now() >= grace_ends_at:
        return downgrade_owner_to_viewer(user)
    return user


def expire_trial_if_needed(user: dict) -> dict:
    plan = user.get("plan")
    if not plan:
        return user

    if plan.get("type") != PlanType.free_trial.value or plan.get("status") != PlanStatus.active.value:
        return user

    ends_at = plan.get("ends_at")
    if ends_at is None:
        return user

    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)

    if utc_now() >= ends_at:
        return downgrade_owner_to_viewer(user)
    return user


def resolve_login_plan_string(user: dict) -> str:
    """Return selected plan slug for login, or empty string when not on free trial/starter."""
    if get_user_role(user) == UserRole.admin:
        return PlanType.premium.value

    plan = user.get("plan") or {}
    plan_type = plan.get("type")
    selected_plan_type = plan.get("selected_plan_type")
    status = plan.get("status", PlanStatus.active.value)

    if plan_type == PlanType.free_trial.value and status == PlanStatus.active.value:
        return PlanType.free_trial.value

    if plan_type == PlanType.starter.value and selected_plan_type == PlanType.starter.value:
        return PlanType.starter.value

    return ""


def build_plan_summary(user: dict) -> PlanSummary:
    if get_user_role(user) == UserRole.admin:
        return admin_plan_summary()

    user = expire_trial_if_needed(user)
    user = expire_paid_plan_if_needed(user)
    user = expire_grace_period_if_needed(user)
    plan = user.get("plan") or default_plan_document()
    try:
        plan_type = PlanType(plan.get("type", PlanType.free_trial.value))
    except ValueError:
        plan_type = PlanType.free_trial
    selected_plan_type = plan.get("selected_plan_type")
    if selected_plan_type is not None:
        try:
            selected_plan_type = PlanType(selected_plan_type)
        except ValueError:
            selected_plan_type = None
    elif is_paid_plan(plan.get("type")):
        selected_plan_type = plan_type
    details = PLAN_CATALOG[plan_type.value]
    try:
        status_value = PlanStatus(plan.get("status", PlanStatus.active.value))
    except ValueError:
        status_value = PlanStatus.active
    started_at = plan.get("started_at", utc_now())
    ends_at = plan.get("ends_at")
    grace_ends_at = plan.get("grace_ends_at")
    in_grace_period = status_value == PlanStatus.grace_period

    days_remaining = None
    is_active = status_value in {PlanStatus.active, PlanStatus.grace_period}

    countdown_end = grace_ends_at if in_grace_period else ends_at
    if countdown_end is not None:
        if countdown_end.tzinfo is None:
            countdown_end = countdown_end.replace(tzinfo=timezone.utc)
        remaining = countdown_end - utc_now()
        days_remaining = max(0, remaining.days + (1 if remaining.seconds > 0 else 0))
        if utc_now() >= countdown_end:
            if in_grace_period or plan_type == PlanType.free_trial:
                is_active = False

    return PlanSummary(
        type=plan_type,
        status=status_value,
        name=details["name"],
        price_inr=details["price_inr"],
        max_products=details["max_products"],
        profile_only=details["profile_only"],
        description=details["description"],
        started_at=started_at,
        ends_at=ends_at,
        days_remaining=days_remaining,
        is_active=is_active,
        trial_used=bool(plan.get("trial_used", False)),
        selected_plan_type=selected_plan_type,
        in_grace_period=in_grace_period,
        grace_ends_at=grace_ends_at,
    )


def require_active_plan(user: dict) -> PlanSummary:
    if get_user_role(user) == UserRole.admin:
        return admin_plan_summary()

    summary = build_plan_summary(user)
    if not summary.is_active:
        if summary.in_grace_period:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your grace period has ended. Please renew your plan to continue.",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your plan has expired. Please choose a plan to continue.",
        )
    return summary


def select_plan_for_user(user_id: ObjectId, plan_type: PlanType) -> PlanSummary:
    user = users.find_one({"_id": user_id})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if get_user_role(user) == UserRole.admin:
        return admin_plan_summary()

    if get_user_role(user) == UserRole.viewer:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Viewers cannot select a plan directly. Join the waitlist to be activated by an admin.",
        )

    existing_plan = user.get("plan") or {}
    if existing_plan.get("viewing_applied"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Viewers cannot select a plan directly. Join the waitlist to be activated by an admin.",
        )

    if plan_type == PlanType.free_trial:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Free trial cannot be selected manually")

    if existing_plan.get("trial_used") and plan_type == PlanType.free_trial:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Free trial has already been used")

    now = utc_now()
    details = PLAN_CATALOG[plan_type.value]
    duration_days = details.get("duration_days")
    ends_at = now + timedelta(days=duration_days) if duration_days else None
    plan_document = {
        "type": plan_type.value,
        "status": PlanStatus.active.value,
        "started_at": existing_plan.get("started_at", now),
        "ends_at": ends_at,
        "grace_ends_at": None,
        "grace_started_at": None,
        "viewing_applied": False,
        "trial_used": bool(existing_plan.get("trial_used", False)),
        "selected_plan_type": plan_type.value,
        "selected_at": now,
    }
    updated = users.find_one_and_update(
        {"_id": user_id},
        {"$set": {"plan": plan_document, "role": UserRole.owner.value, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    return build_plan_summary(updated)


def cancel_plan_for_user(user_id: ObjectId) -> PlanSummary:
    user = users.find_one({"_id": user_id})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if get_user_role(user) == UserRole.admin:
        return admin_plan_summary()

    plan = user.get("plan") or {}
    current_type = plan.get("type")
    if not is_paid_plan(current_type):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Starter, Growth, or Premium plans can enter a grace period",
        )
    if plan.get("status") == PlanStatus.grace_period.value:
        return build_plan_summary(user)

    now = utc_now()
    grace_ends_at = now + timedelta(days=GRACE_DAYS)
    updated = users.find_one_and_update(
        {"_id": user_id},
        {
            "$set": {
                "plan.status": PlanStatus.grace_period.value,
                "plan.selected_plan_type": current_type,
                "plan.grace_started_at": now,
                "plan.grace_ends_at": grace_ends_at,
                "plan.cancelled_at": now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    return build_plan_summary(updated)


def admin_activate_viewer_from_waitlist(user_id: ObjectId) -> PlanSummary:
    """Approve a pending waitlist application and upgrade a viewer to owner with their requested plan."""
    user = users.find_one({"_id": user_id})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if get_user_role(user) == UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin accounts are not activated via the waitlist",
        )

    if get_user_role(user) != UserRole.viewer:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only viewers on the waitlist can be activated. Owners should select a plan at POST /plans/select.",
        )

    application = plan_applications.find_one(
        {"user_id": str(user_id), "status": "pending"},
        # FIFO queue: oldest first
        sort=[("created_at", 1)],
    )
    if application is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no pending waitlist application",
        )

    plan_type_value = application.get("requested_plan_type") or PlanType.starter.value
    if plan_type_value == PlanType.free_trial.value:
        plan_type_value = PlanType.starter.value
    plan_type = PlanType(plan_type_value)

    now = utc_now()
    existing_plan = user.get("plan") or {}
    plan_document = {
        "type": plan_type.value,
        "status": PlanStatus.active.value,
        "started_at": now,
        "ends_at": None,
        "grace_ends_at": None,
        "grace_started_at": None,
        "viewing_applied": False,
        "trial_used": bool(existing_plan.get("trial_used", False)),
        "selected_plan_type": plan_type.value,
        "activated_at": now,
        "activated_by": "admin",
        "selected_at": now,
    }
    updated = users.find_one_and_update(
        {"_id": user_id},
        {
            "$set": {
                "plan": plan_document,
                "role": UserRole.owner.value,
                "updated_at": now,
            },
            "$unset": {
                "account_status": "",
                "pre_deactivation_role": "",
                "plan.deactivated_at": "",
                "plan.deactivated_by": "",
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    plan_applications.update_one(
        {"_id": application["_id"]},
        {"$set": {"status": "approved", "approved_at": now, "updated_at": now}},
    )

    # Keep shop plan in sync with the approved request when a shop is attached.
    shop_id = application.get("shop_id")
    if isinstance(shop_id, str) and shop_id.strip():
        try:
            select_plan_for_shop(shop_id.strip(), plan_type)
        except HTTPException:
            # User activation still succeeds if the shop plan write fails.
            pass

    return build_plan_summary(updated)


def admin_delete_users(user_ids: list[ObjectId]) -> dict:
    deleted_ids: list[str] = []
    not_found_ids: list[str] = []
    protected_owner_ids: list[str] = []
    protected_admin_ids: list[str] = []

    for user_id in user_ids:
        user = users.find_one({"_id": user_id})
        if user is None:
            not_found_ids.append(str(user_id))
            continue

        role = get_user_role(user)
        if role == UserRole.admin:
            protected_admin_ids.append(str(user_id))
            continue
        if role == UserRole.owner:
            protected_owner_ids.append(str(user_id))
            continue
        if role != UserRole.viewer:
            protected_owner_ids.append(str(user_id))
            continue

        result = users.delete_one({"_id": user_id, "role": UserRole.viewer.value})
        if result.deleted_count:
            deleted_ids.append(str(user_id))

    return {
        "deleted_count": len(deleted_ids),
        "deleted_ids": deleted_ids,
        "not_found_ids": not_found_ids,
        "protected_owner_ids": protected_owner_ids,
        "protected_admin_ids": protected_admin_ids,
    }


def admin_delete_viewers(user_ids: list[ObjectId]) -> dict:
    return admin_delete_users(user_ids)


def get_product_limit(user: dict) -> int | None:
    summary = build_plan_summary(user)
    if not summary.is_active:
        return 0
    return summary.max_products


def get_shop_document(store_id: str) -> dict:
    shop = shops.find_one({"_id": parse_object_id(store_id, "Shop")})
    if shop is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    return shop


def ensure_shop_has_plan(shop: dict) -> dict:
    """Attach a plan to legacy shops that do not have one yet."""
    if shop.get("plan"):
        return shop
    plan = admin_shop_plan_document() if shop_owner_is_admin(shop) else default_plan_document()
    updated = shops.find_one_and_update(
        {"_id": shop["_id"]},
        {
            "$set": {
                "plan": plan,
                "is_locked": False if plan.get("type") == PlanType.premium.value else shop.get("is_locked", False),
                "lock_reason": None if plan.get("type") == PlanType.premium.value else shop.get("lock_reason"),
                "updated_at": utc_now(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    return updated or {**shop, "plan": plan}


def heal_admin_owned_shop_plan(shop: dict) -> dict:
    """Ensure admin-owned shops stay on unlocked premium (fixes legacy trial/expired rows)."""
    if not shop_owner_is_admin(shop):
        return shop
    plan = shop.get("plan") or {}
    needs_heal = (
        shop.get("is_locked")
        or plan.get("type") != PlanType.premium.value
        or plan.get("status") != PlanStatus.active.value
        or plan.get("ends_at") is not None
    )
    if not needs_heal:
        return shop
    now = utc_now()
    updated = shops.find_one_and_update(
        {"_id": shop["_id"]},
        {
            "$set": {
                "plan": admin_shop_plan_document(),
                "is_locked": False,
                "lock_reason": None,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    return updated or shop


def sync_owner_viewer_from_shop(shop: dict) -> None:
    """After shop trial/grace expiry, downgrade the linked owner to viewer when safe.

    Conservative: skips admins, non-owners, and owners still on an active paid plan.
    Only acts when the owner is on free_trial or viewing has not yet been applied.
    """
    owner_raw = str(shop.get("owner_user_id") or "").strip()
    if not owner_raw or not ObjectId.is_valid(owner_raw):
        return

    user = users.find_one({"_id": ObjectId(owner_raw)})
    if user is None:
        return
    if get_user_role(user) == UserRole.admin:
        return
    if get_user_role(user) != UserRole.owner:
        return

    plan = user.get("plan") or {}
    if is_paid_plan(plan.get("type")) and plan.get("status") == PlanStatus.active.value:
        return

    if plan.get("type") == PlanType.free_trial.value or not plan.get("viewing_applied"):
        downgrade_owner_to_viewer(user)


def expire_shop_trial_if_needed(shop: dict) -> dict:
    if shop_owner_is_admin(shop):
        return heal_admin_owned_shop_plan(shop)
    plan = shop.get("plan")
    if not plan:
        return shop
    if plan.get("type") != PlanType.free_trial.value or plan.get("status") != PlanStatus.active.value:
        return shop
    ends_at = plan.get("ends_at")
    if ends_at is None:
        return shop
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)
    if utc_now() < ends_at:
        return shop
    updated = shops.find_one_and_update(
        {"_id": shop["_id"], "plan.status": PlanStatus.active.value, "plan.type": PlanType.free_trial.value},
        {
            "$set": {
                "plan.status": PlanStatus.deactivated.value,
                "plan.expired_at": utc_now(),
                "plan.viewing_applied": True,
                "is_locked": True,
                "lock_reason": "plan_expired",
                "updated_at": utc_now(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    result = updated or shop
    sync_owner_viewer_from_shop(result)
    return result


def expire_shop_paid_plan_if_needed(shop: dict) -> dict:
    plan = shop.get("plan")
    if not plan or plan.get("status") != PlanStatus.active.value:
        return shop
    if not is_paid_plan(plan.get("type")):
        return shop
    ends_at = plan.get("ends_at")
    if ends_at is None:
        return shop
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)
    if utc_now() < ends_at:
        return shop
    now = utc_now()
    grace_ends_at = now + timedelta(days=GRACE_DAYS)
    updated = shops.find_one_and_update(
        {"_id": shop["_id"], "plan.status": PlanStatus.active.value},
        {
            "$set": {
                "plan.status": PlanStatus.grace_period.value,
                "plan.selected_plan_type": plan.get("selected_plan_type") or plan.get("type"),
                "plan.grace_started_at": now,
                "plan.grace_ends_at": grace_ends_at,
                "plan.expired_at": now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    return updated or shop


def expire_shop_grace_period_if_needed(shop: dict) -> dict:
    plan = shop.get("plan")
    if not plan or plan.get("status") != PlanStatus.grace_period.value:
        return shop
    grace_ends_at = plan.get("grace_ends_at")
    if grace_ends_at is None:
        return shop
    if grace_ends_at.tzinfo is None:
        grace_ends_at = grace_ends_at.replace(tzinfo=timezone.utc)
    if utc_now() < grace_ends_at:
        return shop
    updated = shops.find_one_and_update(
        {"_id": shop["_id"], "plan.status": PlanStatus.grace_period.value},
        {
            "$set": {
                "plan.status": PlanStatus.deactivated.value,
                "plan.viewing_applied": True,
                "is_locked": True,
                "lock_reason": "plan_expired",
                "updated_at": utc_now(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    result = updated or shop
    sync_owner_viewer_from_shop(result)
    return result


def build_shop_plan_summary(shop: dict) -> PlanSummary:
    shop = ensure_shop_has_plan(shop)
    if shop_owner_is_admin(shop):
        heal_admin_owned_shop_plan(shop)
        return admin_plan_summary()
    shop = expire_shop_trial_if_needed(shop)
    shop = expire_shop_paid_plan_if_needed(shop)
    shop = expire_shop_grace_period_if_needed(shop)
    plan = shop.get("plan") or default_plan_document()
    plan_type = PlanType(plan.get("type", PlanType.free_trial.value))
    selected_plan_type = plan.get("selected_plan_type")
    if selected_plan_type is not None:
        selected_plan_type = PlanType(selected_plan_type)
    elif is_paid_plan(plan.get("type")):
        selected_plan_type = plan_type
    else:
        selected_plan_type = None

    details = PLAN_CATALOG[plan_type.value]
    status = PlanStatus(plan.get("status", PlanStatus.active.value))
    started_at = plan.get("started_at") or utc_now()
    ends_at = plan.get("ends_at")
    grace_ends_at = plan.get("grace_ends_at")
    in_grace_period = status == PlanStatus.grace_period
    is_active = status == PlanStatus.active
    days_remaining = None
    if ends_at is not None:
        if ends_at.tzinfo is None:
            ends_at = ends_at.replace(tzinfo=timezone.utc)
        delta = ends_at - utc_now()
        days_remaining = max(0, delta.days)
        if status == PlanStatus.active and delta.total_seconds() <= 0:
            is_active = False
    if in_grace_period and grace_ends_at is not None:
        if grace_ends_at.tzinfo is None:
            grace_ends_at = grace_ends_at.replace(tzinfo=timezone.utc)
        if utc_now() >= grace_ends_at:
            is_active = False

    return PlanSummary(
        type=plan_type,
        status=status,
        name=details["name"],
        price_inr=details["price_inr"],
        max_products=details["max_products"],
        profile_only=details["profile_only"],
        description=details["description"],
        started_at=started_at,
        ends_at=ends_at,
        days_remaining=days_remaining,
        is_active=is_active,
        trial_used=bool(plan.get("trial_used", False)),
        selected_plan_type=selected_plan_type,
        in_grace_period=in_grace_period,
        grace_ends_at=grace_ends_at,
    )


def require_active_shop_plan(store_id: str) -> tuple[dict, PlanSummary]:
    shop = get_shop_document(store_id)
    summary = build_shop_plan_summary(shop)
    shop = get_shop_document(store_id)
    if not summary.is_active:
        if summary.in_grace_period:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This shop's grace period has ended. Renew the shop plan to continue.",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This shop's plan has expired. Purchase a plan and complete payment to continue.",
        )
    return shop, summary


def select_plan_for_shop(store_id: str, plan_type: PlanType) -> PlanSummary:
    shop = ensure_shop_has_plan(get_shop_document(store_id))
    if plan_type == PlanType.free_trial:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Free trial cannot be selected manually")

    existing_plan = shop.get("plan") or {}
    now = utc_now()
    details = PLAN_CATALOG[plan_type.value]
    duration_days = details.get("duration_days")
    ends_at = now + timedelta(days=duration_days) if duration_days else None
    plan_document = {
        "type": plan_type.value,
        "status": PlanStatus.active.value,
        "started_at": existing_plan.get("started_at", now),
        "ends_at": ends_at,
        "grace_ends_at": None,
        "grace_started_at": None,
        "viewing_applied": False,
        "trial_used": True,
        "selected_plan_type": plan_type.value,
        "selected_at": now,
    }
    updated = shops.find_one_and_update(
        {"_id": shop["_id"]},
        {
            "$set": {
                "plan": plan_document,
                "is_locked": False,
                "lock_reason": None,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    # Selecting a plan for this shop does not unlock sibling shops.
    # Never demote platform admins to owner.
    owner_id = str((updated or shop).get("owner_user_id") or "")
    if owner_id:
        owner = users.find_one({"_id": parse_object_id(owner_id, "User")})
        if owner is None or get_user_role(owner) != UserRole.admin:
            users.update_one(
                {"_id": parse_object_id(owner_id, "User")},
                {
                    "$set": {
                        "role": UserRole.owner.value,
                        "plan.viewing_applied": False,
                        "updated_at": now,
                    }
                },
            )
    return build_shop_plan_summary(updated or shop)


def ensure_can_add_product(user: dict, store_id: str) -> None:
    """Product limits come from the shop's plan (not the user account)."""
    if get_user_role(user) == UserRole.admin:
        return

    _, summary = require_active_shop_plan(store_id)
    if summary.profile_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This shop plan only includes a profile. Upgrade the shop plan to add products.",
        )

    max_products = summary.max_products
    if max_products is None:
        return

    from .product_bucket import get_extra_slots

    current_count = products.count_documents({"store_id": store_id})
    capacity = max_products + get_extra_slots(store_id)
    if current_count >= capacity:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"This shop's {summary.name} plan allows up to {max_products} products"
                f" ({capacity} with bucket slots). "
                "Add more capacity via POST /product-bucket/slots or upgrade the shop plan."
            ),
        )
