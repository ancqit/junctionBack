"""GST on shop payments + cancel/clear/dismiss (no network)."""

import os

from app.shop_payments import GST_RATE, apply_gst
from app.test_user_seed import ensure_test_user


def test_apply_gst_eighteen_percent():
    priced = apply_gst(999)
    assert priced["base_amount_inr"] == 999
    assert priced["gst_rate"] == GST_RATE
    assert priced["gst_amount_inr"] == 180
    assert priced["amount_inr"] == 1179


def test_apply_gst_zero_base():
    priced = apply_gst(0)
    assert priced["amount_inr"] == 0
    assert priced["gst_amount_inr"] == 0


def test_apply_gst_pack_price():
    # Default pack price 999 → GST 180 → Razorpay 1179
    priced = apply_gst(999)
    assert priced["amount_inr"] == 1179


def test_apply_gst_per_line_not_combined():
    """Plan + pack GST is summed per line (matches FE prepTotal)."""
    plan = apply_gst(2999)
    pack = apply_gst(999)
    combined_base = apply_gst(2999 + 999)
    assert plan["gst_amount_inr"] + pack["gst_amount_inr"] == combined_base["gst_amount_inr"]
    assert plan["amount_inr"] + pack["amount_inr"] == combined_base["amount_inr"]


def test_ensure_test_user_noop_without_env(monkeypatch):
    monkeypatch.delenv("TEST_USER_PHONE", raising=False)
    monkeypatch.delenv("TEST_USER_MPIN", raising=False)
    assert ensure_test_user() is None


def test_ensure_test_user_skips_partial_env(monkeypatch):
    monkeypatch.setenv("TEST_USER_PHONE", "+919876543210")
    monkeypatch.delenv("TEST_USER_MPIN", raising=False)
    assert ensure_test_user() is None
