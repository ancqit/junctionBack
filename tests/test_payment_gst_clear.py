"""GST on shop payments + cancel/clear/dismiss (no network)."""

from app.shop_payments import GST_RATE, apply_gst


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
