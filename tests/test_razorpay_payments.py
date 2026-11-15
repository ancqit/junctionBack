"""Razorpay Standard Checkout: signature verify, order create, webhook (no network)."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from unittest.mock import patch

import mongomock
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app import shop_payments as sp
from app.login import create_access_token, hash_password
from app.main import app
from app.rate_limit import limiter

SECRET = "test-secret-that-is-at-least-32-chars-long"
KEY_ID = "rzp_test_key"
KEY_SECRET = "rzp_test_secret"
WEBHOOK_SECRET = "whsec_test_secret"


def _sign_checkout(order_id: str, payment_id: str) -> str:
    body = f"{order_id}|{payment_id}".encode("utf-8")
    return hmac.new(KEY_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _sign_webhook(raw: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()


@pytest.fixture
def db():
    database = mongomock.MongoClient()["junction_test"]
    users = database["users"]
    shops = database["shops"]
    payments = database["shop_payments"]
    products = database["products"]

    owner_id = ObjectId()
    shop_id = ObjectId()
    users.insert_one(
        {
            "_id": owner_id,
            "display_name": "Asha",
            "phone_number": "+919800000099",
            "email": "asha@example.com",
            "password_hash": hash_password("x"),
            "role": "owner",
            "plan": {"type": "free_trial", "status": "active"},
        }
    )
    shops.insert_one(
        {
            "_id": shop_id,
            "owner_user_id": str(owner_id),
            "name": "Asha Mart",
            "city": "Pune",
            "locality": "Kothrud",
            "plan": {"type": "free_trial", "status": "active", "max_products": 40},
            "created_at": datetime.now(timezone.utc),
        }
    )
    payment_id = ObjectId()
    payments.insert_one(
        {
            "_id": payment_id,
            "store_id": str(shop_id),
            "owner_user_id": str(owner_id),
            "kind": "plan",
            "status": "pending",
            "amount_inr": 999,
            "currency": "INR",
            "plan_type": "starter",
            "packs": None,
            "slots": None,
            "description": "Starter plan",
            "payment_method": None,
            "payment_reference": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "paid_at": None,
            "fulfilled_at": None,
        }
    )

    with (
        patch.object(sp, "shop_payments", payments),
        patch.object(sp, "products", products),
        patch.object(sp, "users", users),
        patch("app.access_control.shops", shops),
        patch("app.plan_service.shops", shops),
        patch("app.plan_service.users", users),
        patch("app.login.users", users),
        patch("app.login.JWT_SECRET", SECRET),
        patch("app.login.restore_persisted_plan", side_effect=lambda user: user),
        patch.object(sp, "RAZORPAY_KEY_ID", KEY_ID),
        patch("app.razorpay_client.RAZORPAY_KEY_ID", KEY_ID),
        patch("app.razorpay_client.RAZORPAY_KEY_SECRET", KEY_SECRET),
        patch("app.razorpay_client.RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET),
        patch.object(limiter, "enabled", False),
    ):
        yield {
            "users": users,
            "shops": shops,
            "payments": payments,
            "owner_id": owner_id,
            "shop_id": shop_id,
            "payment_id": payment_id,
        }


@pytest.fixture
def client():
    return TestClient(app)


def _auth(owner_id: ObjectId) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(owner_id)}"}


def test_checkout_signature_helper_accepts_valid_hmac():
    order_id = "order_abc"
    pay_id = "pay_xyz"
    sig = _sign_checkout(order_id, pay_id)
    with (
        patch("app.razorpay_client.RAZORPAY_KEY_ID", KEY_ID),
        patch("app.razorpay_client.RAZORPAY_KEY_SECRET", KEY_SECRET),
    ):
        from app.razorpay_client import verify_checkout_signature

        assert verify_checkout_signature(
            razorpay_order_id=order_id,
            razorpay_payment_id=pay_id,
            razorpay_signature=sig,
        )
        assert not verify_checkout_signature(
            razorpay_order_id=order_id,
            razorpay_payment_id=pay_id,
            razorpay_signature="deadbeef",
        )


def test_create_razorpay_order_stores_order_id(db, client):
    payment_id = str(db["payment_id"])
    fake_order = {"id": "order_R1", "amount": 99900, "currency": "INR"}

    with patch("app.shop_payments.create_order", return_value=fake_order) as create_mock:
        response = client.post(
            f"/payments/{payment_id}/razorpay/order",
            headers=_auth(db["owner_id"]),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["order_id"] == "order_R1"
    assert body["key_id"] == KEY_ID
    assert body["amount_paise"] == 99900
    assert body["payment_id"] == payment_id
    create_mock.assert_called_once()
    doc = db["payments"].find_one({"_id": db["payment_id"]})
    assert doc["razorpay_order_id"] == "order_R1"


def _starter_summary():
    from app.plan_service import PlanStatus, PlanSummary, PlanType

    now = datetime.now(timezone.utc)
    return PlanSummary(
        type=PlanType.starter,
        status=PlanStatus.active,
        name="Starter",
        price_inr=999,
        max_products=10,
        profile_only=False,
        description="Starter plan",
        started_at=now,
        ends_at=None,
        days_remaining=365,
        is_active=True,
        trial_used=True,
    )


def test_confirm_requires_valid_signature_then_fulfills(db, client):
    payment_id = str(db["payment_id"])
    db["payments"].update_one(
        {"_id": db["payment_id"]},
        {"$set": {"razorpay_order_id": "order_R2"}},
    )
    order_id = "order_R2"
    rz_pay = "pay_OK"
    good = {
        "razorpay_order_id": order_id,
        "razorpay_payment_id": rz_pay,
        "razorpay_signature": _sign_checkout(order_id, rz_pay),
    }
    bad = {**good, "razorpay_signature": "nope"}

    with patch("app.shop_payments.select_plan_for_shop", return_value=_starter_summary()):
        denied = client.post(
            f"/payments/{payment_id}/razorpay/confirm",
            headers=_auth(db["owner_id"]),
            json=bad,
        )
        assert denied.status_code == 400

        ok = client.post(
            f"/payments/{payment_id}/razorpay/confirm",
            headers=_auth(db["owner_id"]),
            json=good,
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["payment"]["status"] == "paid"

    doc = db["payments"].find_one({"_id": db["payment_id"]})
    assert doc["status"] == "paid"
    assert doc["razorpay_payment_id"] == rz_pay
    assert doc["razorpay_source"] == "checkout"


def test_webhook_payment_captured_fulfills(db, client):
    db["payments"].update_one(
        {"_id": db["payment_id"]},
        {"$set": {"razorpay_order_id": "order_WH"}},
    )
    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_WH1",
                    "order_id": "order_WH",
                    "status": "captured",
                    "amount": 99900,
                }
            }
        },
    }
    raw = json.dumps(payload).encode("utf-8")

    with patch("app.shop_payments.select_plan_for_shop", return_value=_starter_summary()):
        response = client.post(
            "/payments/razorpay/webhook",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": _sign_webhook(raw),
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "paid"
    doc = db["payments"].find_one({"_id": db["payment_id"]})
    assert doc["status"] == "paid"
    assert doc["razorpay_source"] == "webhook"


def test_webhook_rejects_bad_signature(db, client):
    raw = b'{"event":"payment.captured","payload":{"payment":{"entity":{"order_id":"x"}}}}'
    response = client.post(
        "/payments/razorpay/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": "bad"},
    )
    assert response.status_code == 400
