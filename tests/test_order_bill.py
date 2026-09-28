"""Order bill PDF: layout helpers, rendering, and who may download it (no Mongo required)."""

from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.order_bill import bill_filename, bill_input_from_order, format_money, gst_label, render_order_bill

ORDER_ID = "665f1c2a9b1e8a0012345679"
STORE_ID = "665f1c2a9b1e8a0012345678"
BILL_TOKEN = "customer-bill-token"

SHOP = {
    "name": "Lane Oyster Co-op",
    "address": "7 Grove Lane",
    "locality": "Indiranagar",
    "city": "Bengaluru",
    "gst_verified": True,
    "gstin": "29ABCDE1234F1Z5",
}
ORDER = {
    "order_number": "ORD-20260928163000-A1B2C3",
    "store_id": STORE_ID,
    "bill_token": BILL_TOKEN,
    "customer_name": "रीना शर्मा",
    "customer_phone": "+919812345678",
    "created_at": datetime(2026, 9, 28, 11, 0, tzinfo=timezone.utc),
    "status": "pending",
    "source": "junction.today",
    "notes": None,
    "items": [{"product_name": "ताज़ा ऑयस्टर मशरूम", "quantity": 3, "unit_price": 60.0}],
    "billing": {
        "subtotal": 180.0,
        "tax_amount": 9.0,
        "total_amount": 189.0,
        "currency": "INR",
        "payment_method": "cash",
        "payment_status": "pending",
    },
}


def test_format_money_uses_indian_grouping():
    assert format_money(1234567.5, "INR") == "₹ 12,34,567.50"
    assert format_money(999, "INR") == "₹ 999.00"
    assert format_money(1500, "usd") == "USD 1,500.00"


def test_gst_label_shows_rate_from_amounts():
    assert gst_label("GST", 9, 180) == "GST (5%)"
    assert gst_label("GST", 0, 0) == "GST"


def test_bill_input_reads_shop_and_links_qr_to_shop():
    bill = bill_input_from_order(ORDER, SHOP)
    assert bill.shop_address == "7 Grove Lane, Indiranagar, Bengaluru"
    assert bill.gstin == "29ABCDE1234F1Z5"
    assert f"store_id={STORE_ID}" in bill.qr_url
    assert bill.host == "junction.today"


def test_bill_input_hides_unverified_gstin_and_survives_missing_shop():
    assert bill_input_from_order(ORDER, {**SHOP, "gst_verified": False}).gstin is None
    bill = bill_input_from_order({**ORDER, "source": "junction.earth"}, None)
    assert bill.shop_name == "Junction shop"
    assert bill.host == "junction.earth"


def test_render_produces_pdf_in_both_languages():
    bill = bill_input_from_order(ORDER, SHOP)
    for lang in ("en", "hi"):
        assert render_order_bill(bill, lang).startswith(b"%PDF")


def test_bill_filename_is_header_safe():
    assert bill_filename('ORD-1"; x') == "junction-bill-ORD-1---x.pdf"


def _get_bill(client: TestClient, **kwargs):
    with patch("app.orders.get_order_or_404", return_value=dict(ORDER)), patch(
        "app.orders.get_shop_by_store_id", return_value=dict(SHOP)
    ):
        return client.get(f"/orders/{ORDER_ID}/bill.pdf", **kwargs)


def test_bill_download_with_customer_token():
    response = _get_bill(TestClient(app), params={"token": BILL_TOKEN, "lang": "hi"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.content.startswith(b"%PDF")


def test_bill_download_refuses_missing_or_wrong_token():
    client = TestClient(app)
    assert _get_bill(client).status_code == 401
    assert _get_bill(client, params={"token": "wrong"}).status_code == 401


def test_bill_download_with_shop_owner_login():
    client = TestClient(app)
    with patch("app.orders.get_current_user", return_value={"_id": "owner"}), patch(
        "app.orders.require_store_access"
    ) as access:
        response = _get_bill(client, headers={"Authorization": "Bearer owner-jwt"})
    assert response.status_code == 200
    access.assert_called_once_with({"_id": "owner"}, STORE_ID)


def test_bill_download_refuses_other_shop_owner():
    client = TestClient(app)
    with patch("app.orders.get_current_user", return_value={"_id": "stranger"}), patch(
        "app.orders.require_store_access", side_effect=HTTPException(status_code=403, detail="Forbidden")
    ):
        response = _get_bill(client, headers={"Authorization": "Bearer stranger-jwt"})
    assert response.status_code == 403
