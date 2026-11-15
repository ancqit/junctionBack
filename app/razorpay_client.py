"""Razorpay Standard Checkout helpers (Orders API + HMAC verify + webhook auth)."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

import httpx
from fastapi import HTTPException, status

RAZORPAY_KEY_ID = (os.getenv("RAZORPAY_KEY_ID") or "").strip()
RAZORPAY_KEY_SECRET = (os.getenv("RAZORPAY_KEY_SECRET") or "").strip()
RAZORPAY_WEBHOOK_SECRET = (os.getenv("RAZORPAY_WEBHOOK_SECRET") or "").strip()
RAZORPAY_ORDERS_URL = "https://api.razorpay.com/v1/orders"


def razorpay_configured() -> bool:
    return bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)


def webhook_configured() -> bool:
    return bool(RAZORPAY_WEBHOOK_SECRET)


def require_razorpay() -> None:
    if not razorpay_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Razorpay is not configured (set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET).",
        )


def amount_to_paise(amount_inr: int) -> int:
    if amount_inr <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment amount must be greater than zero.",
        )
    return int(amount_inr) * 100


def create_order(
    *,
    amount_inr: int,
    receipt: str,
    notes: dict[str, str] | None = None,
    currency: str = "INR",
) -> dict[str, Any]:
    """Create a Razorpay order. Amount is INR rupees; sent to API as paise."""
    require_razorpay()
    payload: dict[str, Any] = {
        "amount": amount_to_paise(amount_inr),
        "currency": currency,
        "receipt": receipt[:40],
        "payment_capture": 1,
    }
    if notes:
        payload["notes"] = {str(k): str(v)[:256] for k, v in list(notes.items())[:15]}

    try:
        response = httpx.post(
            RAZORPAY_ORDERS_URL,
            json=payload,
            auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach Razorpay Orders API: {exc}",
        ) from exc

    if response.status_code >= 400:
        detail = "Razorpay order create failed"
        try:
            body = response.json()
            detail = str(body.get("error", {}).get("description") or body.get("error") or detail)
        except Exception:
            detail = (response.text or detail)[:300]
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)

    data = response.json()
    order_id = str(data.get("id") or "").strip()
    if not order_id:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Razorpay order create returned no order id",
        )
    return data


def verify_checkout_signature(
    *,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
) -> bool:
    """HMAC-SHA256 of `order_id|payment_id` with KEY_SECRET (Standard Checkout handler)."""
    require_razorpay()
    order_id = (razorpay_order_id or "").strip()
    payment_id = (razorpay_payment_id or "").strip()
    signature = (razorpay_signature or "").strip()
    if not order_id or not payment_id or not signature:
        return False
    body = f"{order_id}|{payment_id}".encode("utf-8")
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(*, raw_body: bytes, signature_header: str | None) -> bool:
    """HMAC-SHA256 of the raw webhook body with RAZORPAY_WEBHOOK_SECRET."""
    if not webhook_configured():
        return False
    signature = (signature_header or "").strip()
    if not signature:
        return False
    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
