"""Unit checks for plan enforcement truths (no Mongo required for these)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.plan_service import (
    PLAN_CATALOG,
    PlanStatus,
    PlanType,
    TRIAL_DAYS,
    activate_selected_plan_after_shop_trial,
    expire_shop_trial_if_needed,
    restore_persisted_plan,
    select_plan_for_user,
    shop_trial_due_at,
)


def test_restore_persisted_plan_does_not_promote_selected_starter():
    """Unpaid selected_plan_type must not become forever Starter on login."""
    user = {
        "_id": "abc",
        "role": "owner",
        "plan": {
            "type": PlanType.free_trial.value,
            "status": PlanStatus.active.value,
            "selected_plan_type": PlanType.starter.value,
            "ends_at": datetime.now(timezone.utc) + timedelta(days=10),
            "viewing_applied": False,
        },
    }
    with patch("app.plan_service.users") as users_mock, patch(
        "app.plan_service.expire_trial_if_needed", side_effect=lambda u: u
    ), patch("app.plan_service.expire_paid_plan_if_needed", side_effect=lambda u: u), patch(
        "app.plan_service.expire_grace_period_if_needed", side_effect=lambda u: u
    ):
        users_mock.find_one.return_value = user
        out = restore_persisted_plan(user)
    assert out["plan"]["type"] == PlanType.free_trial.value
    assert out["plan"]["selected_plan_type"] == PlanType.starter.value
    users_mock.find_one_and_update.assert_not_called()


def test_select_plan_for_user_is_intent_only():
    user_id = MagicMock()
    existing = {
        "_id": user_id,
        "role": "owner",
        "plan": {
            "type": PlanType.free_trial.value,
            "status": PlanStatus.active.value,
            "ends_at": datetime.now(timezone.utc) + timedelta(days=5),
            "trial_used": True,
            "viewing_applied": False,
        },
    }
    updated = {
        **existing,
        "plan": {
            **existing["plan"],
            "selected_plan_type": PlanType.starter.value,
            "selected_at": datetime.now(timezone.utc),
        },
    }
    with patch("app.plan_service.users") as users_mock, patch(
        "app.plan_service.build_plan_summary"
    ) as summary_mock, patch("app.plan_service.mirror_selected_plan_to_trial_shops") as mirror_mock:
        users_mock.find_one.return_value = existing
        users_mock.find_one_and_update.return_value = updated
        summary_mock.return_value = MagicMock()
        select_plan_for_user(user_id, PlanType.starter)
        set_fields = users_mock.find_one_and_update.call_args[0][1]["$set"]
        assert set_fields["plan.selected_plan_type"] == PlanType.starter.value
        assert "plan.type" not in set_fields
        assert "type" not in set_fields.get("plan", {})
        mirror_mock.assert_called_once()


def test_shop_trial_due_at_uses_created_at_plus_trial_days():
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    shop = {"created_at": created, "plan": {"ends_at": created + timedelta(days=3)}}
    assert shop_trial_due_at(shop) == created + timedelta(days=TRIAL_DAYS)


def test_activate_selected_plan_after_shop_trial_promotes_intent():
    created = datetime.now(timezone.utc) - timedelta(days=TRIAL_DAYS + 1)
    shop = {
        "_id": "shop1",
        "created_at": created,
        "owner_user_id": "owner1",
        "plan": {
            "type": PlanType.free_trial.value,
            "status": PlanStatus.active.value,
            "selected_plan_type": PlanType.starter.value,
        },
    }
    activated = {
        **shop,
        "plan": {
            "type": PlanType.starter.value,
            "status": PlanStatus.active.value,
            "selected_plan_type": PlanType.starter.value,
        },
    }
    with patch("app.plan_service.shop_owner_is_admin", return_value=False), patch(
        "app.plan_service.select_plan_for_shop"
    ) as select_mock, patch("app.plan_service.shops") as shops_mock:
        select_mock.return_value = MagicMock(
            max_products=PLAN_CATALOG[PlanType.starter.value]["max_products"]
        )
        shops_mock.find_one.return_value = activated
        out, did = activate_selected_plan_after_shop_trial(shop)
    assert did is True
    assert out["plan"]["type"] == PlanType.starter.value
    select_mock.assert_called_once_with("shop1", PlanType.starter)


def test_expire_shop_trial_without_selected_goes_viewer():
    created = datetime.now(timezone.utc) - timedelta(days=TRIAL_DAYS + 1)
    shop = {
        "_id": "shop2",
        "created_at": created,
        "owner_user_id": "owner2",
        "plan": {
            "type": PlanType.free_trial.value,
            "status": PlanStatus.active.value,
            "selected_plan_type": None,
        },
    }
    viewer_shop = {
        **shop,
        "plan": {
            **shop["plan"],
            "status": PlanStatus.deactivated.value,
            "viewing_applied": True,
        },
        "is_locked": True,
        "lock_reason": "plan_expired",
    }
    with patch("app.plan_service.shop_owner_is_admin", return_value=False), patch(
        "app.plan_service.activate_selected_plan_after_shop_trial", return_value=(shop, False)
    ), patch("app.plan_service.shops") as shops_mock, patch(
        "app.plan_service.sync_owner_viewer_from_shop"
    ), patch("app.plan_service.close_storefront_if_viewer_due", side_effect=lambda s: s):
        shops_mock.find_one_and_update.return_value = viewer_shop
        out = expire_shop_trial_if_needed(shop)
    assert out["plan"]["status"] == PlanStatus.deactivated.value
    assert out.get("is_locked") is True
