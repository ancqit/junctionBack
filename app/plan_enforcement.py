"""Daily plan cron helpers: trial → selected plan, expire → viewer → close, viewer-day logs.

Does not delete products or product_buckets (data retained until new plan payment).
"""

from __future__ import annotations

from datetime import datetime, timezone

from .database import shop_viewer_day_logs, shops, users
from .plan_service import (
    PLAN_CATALOG,
    PlanStatus,
    PlanType,
    TRIAL_DAYS,
    activate_selected_plan_after_shop_trial,
    close_storefront_if_viewer_due,
    expire_grace_period_if_needed,
    expire_paid_plan_if_needed,
    expire_shop_grace_period_if_needed,
    expire_shop_paid_plan_if_needed,
    expire_shop_trial_if_needed,
    expire_trial_if_needed,
    is_paid_plan,
    shop_trial_due_at,
    viewer_mode_started_at,
)
from .roles import UserRole, get_user_role


def _utc_day(now: datetime | None = None) -> str:
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _shop_in_viewer(shop: dict) -> bool:
    plan = shop.get("plan") or {}
    return bool(plan.get("viewing_applied")) or (
        bool(shop.get("is_locked")) and shop.get("lock_reason") == "plan_expired"
    )


def log_shop_viewer_day(shop: dict, now: datetime | None = None) -> bool:
    """Upsert one viewer-day row for today. Returns True if a row was written."""
    if not _shop_in_viewer(shop):
        return False
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    day = _utc_day(stamp)
    started = viewer_mode_started_at(shop)
    days_elapsed = 0
    if started is not None:
        days_elapsed = max(0, (stamp - started).days)
    plan = shop.get("plan") or {}
    plan_type = plan.get("type")
    closed = shop.get("is_open") is False or shop.get("closed_for_viewer_at") is not None
    shop_viewer_day_logs.update_one(
        {"shop_id": str(shop["_id"]), "day": day},
        {
            "$set": {
                "shop_id": str(shop["_id"]),
                "owner_user_id": str(shop.get("owner_user_id") or ""),
                "day": day,
                "days_elapsed": days_elapsed,
                "closed_for_viewer": closed,
                "plan_type": plan_type,
                "plan_status": plan.get("status"),
                "is_locked": bool(shop.get("is_locked")),
                "lock_reason": shop.get("lock_reason"),
                "is_open": bool(shop.get("is_open", True)),
                "status_snapshot": {
                    "plan_type": plan_type,
                    "plan_status": plan.get("status"),
                    "viewing_applied": bool(plan.get("viewing_applied")),
                    "is_locked": bool(shop.get("is_locked")),
                    "lock_reason": shop.get("lock_reason"),
                    "is_open": bool(shop.get("is_open", True)),
                },
                "logged_at": stamp,
            }
        },
        upsert=True,
    )
    return True


def list_shop_viewer_day_logs(shop_id: str, *, limit: int = 90) -> list[dict]:
    cursor = (
        shop_viewer_day_logs.find({"shop_id": shop_id})
        .sort("day", -1)
        .limit(max(1, min(limit, 366)))
    )
    rows: list[dict] = []
    for doc in cursor:
        rows.append(
            {
                "shop_id": doc.get("shop_id"),
                "owner_user_id": doc.get("owner_user_id"),
                "day": doc.get("day"),
                "days_elapsed": doc.get("days_elapsed"),
                "closed_for_viewer": bool(doc.get("closed_for_viewer")),
                "plan_type": doc.get("plan_type"),
                "plan_status": doc.get("plan_status"),
                "logged_at": doc.get("logged_at"),
                "status_snapshot": doc.get("status_snapshot") or {},
            }
        )
    return rows


def promote_due_trial_shops_to_selected_plan(*, now: datetime | None = None) -> dict:
    """Cron helper: shops past created_at + TRIAL_DAYS with a selected plan → activate that plan.

    Free trial was active; selected plan becomes active; product capacity follows that plan only.
    """
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    scanned = 0
    activated = 0
    for shop in shops.find(
        {
            "plan.type": PlanType.free_trial.value,
            "plan.status": PlanStatus.active.value,
        }
    ):
        scanned += 1
        due_at = shop_trial_due_at(shop)
        if due_at is None or stamp < due_at:
            continue
        _, did = activate_selected_plan_after_shop_trial(shop)
        if did:
            activated += 1
    return {
        "trial_shops_scanned": scanned,
        "plans_activated": activated,
        "trial_days": TRIAL_DAYS,
        "ran_at": stamp.isoformat(),
    }


def enforce_plans_once() -> dict:
    """Sweep: promote due trial→selected plan, then expire users/shops → viewer → close."""
    now = datetime.now(timezone.utc)
    users_scanned = 0
    shops_scanned = 0
    downgraded = 0
    closed = 0
    log_rows = 0

    # Promote first so user-trial lock does not park shops that already chose a plan.
    promote = promote_due_trial_shops_to_selected_plan(now=now)
    plans_activated = int(promote.get("plans_activated") or 0)

    for user in users.find({}):
        if get_user_role(user) == UserRole.admin:
            continue
        users_scanned += 1
        before_role = get_user_role(user)
        before_viewer = bool((user.get("plan") or {}).get("viewing_applied"))
        refreshed = expire_trial_if_needed(user)
        refreshed = expire_paid_plan_if_needed(refreshed)
        refreshed = expire_grace_period_if_needed(refreshed)
        after_viewer = bool((refreshed.get("plan") or {}).get("viewing_applied"))
        if (not before_viewer and after_viewer) or (
            before_role == UserRole.owner and get_user_role(refreshed) == UserRole.viewer
        ):
            downgraded += 1

    for shop in shops.find({}):
        shops_scanned += 1
        was_open = shop.get("is_open") is not False
        in_viewer_before = _shop_in_viewer(shop)
        before_type = (shop.get("plan") or {}).get("type")
        refreshed = expire_shop_trial_if_needed(shop)
        after_type = (refreshed.get("plan") or {}).get("type")
        if before_type == PlanType.free_trial.value and is_paid_plan(after_type):
            plans_activated += 1
        refreshed = expire_shop_paid_plan_if_needed(refreshed)
        refreshed = expire_shop_grace_period_if_needed(refreshed)
        refreshed = close_storefront_if_viewer_due(refreshed)
        if was_open and refreshed.get("is_open") is False and _shop_in_viewer(refreshed):
            closed += 1
        if _shop_in_viewer(refreshed):
            if log_shop_viewer_day(refreshed, now):
                log_rows += 1
        elif in_viewer_before and log_shop_viewer_day(refreshed, now):
            log_rows += 1

    return {
        "users_scanned": users_scanned,
        "shops_scanned": shops_scanned,
        "downgraded": downgraded,
        "closed": closed,
        "log_rows": log_rows,
        "plans_activated": plans_activated,
        "trial_days": TRIAL_DAYS,
        "ran_at": now.isoformat(),
        "catalog_trial_name": PLAN_CATALOG[PlanType.free_trial.value]["name"],
    }


def ensure_viewer_day_log_indexes() -> None:
    shop_viewer_day_logs.create_index(
        [("shop_id", 1), ("day", 1)],
        unique=True,
        name="shop_viewer_day_unique",
    )
