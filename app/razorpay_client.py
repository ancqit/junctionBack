"""Razorpay helpers for shop plan and product-pack collection."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

import httpx
from fastapi import HTTPException, status

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"
PROVIDER = "razorpay"


def razorpay_key_id() -> str:
    return (os.getenv("RAZORPAY_KEY_ID") or "").strip()


def razorpay_key_secret() -> str:
    return (os.getenv("RAZORPAY_KEY_SECRET") or "").strip()


def razorpay_webhook_secret() -> str:
    return (os.getenv("RAZORPAY_WEBHOOK_SECRET") or "").strip()


def razorpay_configured() -> bool:
    return bool(razorpay_key_id() and razorpay_key_secret())


def require_razorpay() -> tuple[str, str]:
    key_id = razorpay_key_id()
    key_secret = razorpay_key_secret()
    if not key_id or not key_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Razorpay is not configured. Set RAZORPAY_KEY_ID and "
                "RAZORPAY_KEY_SECRET on the server."
            ),
        )
    return key_id, key_secret


def amount_to_paise(amount_inr: int) -> int:
    if amount_inr <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Amount must be greater than zero for Razorpay checkout.",
        )
    return int(amount_inr) * 100


def create_razorpay_order(
    *,
    amount_inr: int,
    receipt: str,
    notes: dict[str, str] | None = None,
) -> dict[str, Any]:
    key_id, key_secret = require_razorpay()
    payload = {
        "amount": amount_to_paise(amount_inr),
        "currency": "INR",
        "receipt": receipt[:40],
        "payment_capture": 1,
        "notes": notes or {},
    }
    try:
        response = httpx.post(
            f"{RAZORPAY_API_BASE}/orders",
            json=payload,
            auth=(key_id, key_secret),
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach Razorpay: {exc}",
        ) from exc

    if response.status_code >= 400:
        detail = _razorpay_error_detail(response)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Razorpay order creation failed: {detail}",
        )
    data = response.json()
    order_id = data.get("id")
    if not order_id:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Razorpay did not return an order id.",
        )
    return data


def verify_payment_signature(
    *,
    order_id: str,
    payment_id: str,
    signature: str,
) -> None:
    _, key_secret = require_razorpay()
    payload = f"{order_id}|{payment_id}".encode("utf-8")
    expected = hmac.new(
        key_secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, (signature or "").strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Razorpay payment signature.",
        )


def verify_webhook_signature(*, body: bytes, signature: str) -> None:
    secret = razorpay_webhook_secret()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RAZORPAY_WEBHOOK_SECRET is not configured.",
        )
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, (signature or "").strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Razorpay webhook signature.",
        )


def parse_webhook_payload(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Razorpay webhook JSON.",
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Razorpay webhook payload.",
        )
    return data


def extract_captured_payment(payload: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Return (event, order_id, payment_id) for payment.captured / payment.authorized."""
    event = str(payload.get("event") or "")
    payment_entity = (
        ((payload.get("payload") or {}).get("payment") or {}).get("entity") or {}
    )
    if not isinstance(payment_entity, dict):
        return event, None, None
    order_id = payment_entity.get("order_id")
    payment_id = payment_entity.get("id")
    return (
        event,
        str(order_id) if order_id else None,
        str(payment_id) if payment_id else None,
    )


def _razorpay_error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:300] or f"HTTP {response.status_code}"
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        description = error.get("description") or error.get("code")
        if description:
            return str(description)
    return response.text[:300] or f"HTTP {response.status_code}"
