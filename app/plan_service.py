import os
from datetime import datetime, timedelta, timezone
from enum import Enum

from bson import ObjectId
from fastapi import HTTPException, status
from pydantic import BaseModel
from pymongo import ReturnDocument

from .database import products, users

TRIAL_DAYS = int(os.getenv("PLAN_TRIAL_DAYS", "15"))
GRACE_DAYS = int(os.getenv("PLAN_GRACE_DAYS", "15"))


class PlanType(str, Enum):
    free_trial = "free_trial"
    starter = "starter"
    growth = "growth"
    premium = "premium"


class PlanStatus(str, Enum):
    active = "active"
    grace_period = "grace_period"
    expired = "expired"
    cancelled = "cancelled"


PLAN_CATALOG: dict[str, dict] = {
    PlanType.free_trial.value: {
        "name": "Free Trial",
        "price_inr": 0,
        "max_products": 150,
        "profile_only": False,
        "description": "Full access for 15 days",
        "duration_days": TRIAL_DAYS,
    },
    PlanType.starter.value: {
        "name": "Starter",
        "price_inr": 0,
        "max_products": 0,
        "profile_only": True,
        "description": "Profile only",
        "duration_days": None,
    },
    PlanType.growth.value: {
        "name": "Growth",
        "price_inr": 399,
        "max_products": 100,
        "profile_only": False,
        "description": "Add up to 100 products",
        "duration_days": None,
    },
    PlanType.premium.value: {
        "name": "Premium",
        "price_inr": 599,
        "max_products": None,
        "profile_only": False,
        "description": "Add more than 150 products",
        "duration_days": None,
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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def is_paid_plan(plan_type: str | None) -> bool:
    return plan_type in {
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
    """Reload plan from DB and apply trial or grace-period expiry checks."""
    refreshed = users.find_one({"_id": user["_id"]})
    if refreshed is None:
        return user

    plan = refreshed.get("plan")
    if plan is None:
        initialize_user_plan(refreshed["_id"])
        refreshed = users.find_one({"_id": user["_id"]}) or refreshed

    refreshed = expire_trial_if_needed(refreshed)
    return expire_grace_period_if_needed(refreshed)


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
        updated = users.find_one_and_update(
            {"_id": user["_id"], "plan.status": PlanStatus.grace_period.value},
            {
                "$set": {
                    "plan.status": PlanStatus.expired.value,
                    "plan.closed_at": utc_now(),
                    "updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return updated or user
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
        updated = users.find_one_and_update(
            {"_id": user["_id"], "plan.type": PlanType.free_trial.value},
            {
                "$set": {
                    "plan.status": PlanStatus.expired.value,
                    "plan.closed_at": utc_now(),
                    "updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return updated or user
    return user


def build_plan_summary(user: dict) -> PlanSummary:
    user = expire_trial_if_needed(user)
    user = expire_grace_period_if_needed(user)
    plan = user.get("plan") or default_plan_document()
    plan_type = PlanType(plan.get("type", PlanType.free_trial.value))
    selected_plan_type = plan.get("selected_plan_type")
    if selected_plan_type is not None:
        selected_plan_type = PlanType(selected_plan_type)
    elif is_paid_plan(plan.get("type")):
        selected_plan_type = plan_type
    details = PLAN_CATALOG[plan_type.value]
    status_value = PlanStatus(plan.get("status", PlanStatus.active.value))
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
    if plan_type == PlanType.free_trial:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Free trial cannot be selected manually")

    user = users.find_one({"_id": user_id})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    existing_plan = user.get("plan") or {}
    if existing_plan.get("trial_used") and plan_type == PlanType.free_trial:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Free trial has already been used")

    now = utc_now()
    plan_document = {
        "type": plan_type.value,
        "status": PlanStatus.active.value,
        "started_at": existing_plan.get("started_at", now),
        "ends_at": None,
        "grace_ends_at": None,
        "grace_started_at": None,
        "trial_used": bool(existing_plan.get("trial_used", False)),
        "selected_plan_type": plan_type.value,
        "selected_at": now,
    }
    updated = users.find_one_and_update(
        {"_id": user_id},
        {"$set": {"plan": plan_document, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    return build_plan_summary(updated)


def cancel_plan_for_user(user_id: ObjectId) -> PlanSummary:
    user = users.find_one({"_id": user_id})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

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


def get_product_limit(user: dict) -> int | None:
    summary = build_plan_summary(user)
    if not summary.is_active:
        return 0
    return summary.max_products


def ensure_can_add_product(user: dict, store_id: str) -> None:
    summary = require_active_plan(user)
    if summary.profile_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your Starter plan only includes a profile. Upgrade to add products.",
        )

    max_products = summary.max_products
    if max_products is None:
        return

    current_count = products.count_documents({"store_id": store_id})
    if current_count >= max_products:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your {summary.name} plan allows up to {max_products} products.",
        )
