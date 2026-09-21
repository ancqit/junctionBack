"""Unit checks for plan enforcement truths (no Mongo required for these)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.plan_service import (
    PlanStatus,
    PlanType,
    restore_persisted_plan,
    select_plan_for_user,
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
    ) as summary_mock:
        users_mock.find_one.return_value = existing
        users_mock.find_one_and_update.return_value = updated
        summary_mock.return_value = MagicMock()
        select_plan_for_user(user_id, PlanType.starter)
        set_fields = users_mock.find_one_and_update.call_args[0][1]["$set"]
        assert set_fields["plan.selected_plan_type"] == PlanType.starter.value
        assert "plan.type" not in set_fields
        assert "type" not in set_fields.get("plan", {})
