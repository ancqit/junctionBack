"""WhatsApp Meta Cloud API OTP for junction.today checkout verification.

Uses an approved AUTHENTICATION template. Unlike owner login (GCP SMS),
codes are generated and verified by Junction, then delivered via WhatsApp.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from .database import whatsapp_otp_requests
from .rate_limit import RATE_LIMIT_AUTH, limiter

router = APIRouter(prefix="/auth/whatsapp-otp", tags=["whatsapp-otp"])

OTP_EXPIRE_MINUTES = int(os.getenv("OTP_EXPIRE_MINUTES", "5"))
OTP_MAX_ATTEMPTS = int(os.getenv("WHATSAPP_OTP_MAX_ATTEMPTS", "5"))
WHATSAPP_TOKEN = (os.getenv("WHATSAPP_TOKEN") or os.getenv("META_WHATSAPP_TOKEN") or "").strip()
WHATSAPP_PHONE_NUMBER_ID = (
    os.getenv("WHATSAPP_PHONE_NUMBER_ID") or os.getenv("META_WHATSAPP_PHONE_NUMBER_ID") or ""
).strip()
WHATSAPP_OTP_TEMPLATE_NAME = (os.getenv("WHATSAPP_OTP_TEMPLATE_NAME") or "junction_otp").strip()
WHATSAPP_OTP_TEMPLATE_LANGUAGE = (os.getenv("WHATSAPP_OTP_TEMPLATE_LANGUAGE") or "en_US").strip()
WHATSAPP_GRAPH_API_VERSION = (os.getenv("WHATSAPP_GRAPH_API_VERSION") or "v21.0").strip()
WHATSAPP_OTP_INCLUDE_BUTTON = (os.getenv("WHATSAPP_OTP_INCLUDE_BUTTON", "true") or "true").strip().lower() in {
    "1",
    "true",
    "yes",
}
WHATSAPP_OTP_DEBUG = (os.getenv("WHATSAPP_OTP_DEBUG", "false") or "false").strip().lower() in {
    "1",
    "true",
    "yes",
}

_PHONE_DIGITS = re.compile(r"\D+")


class WhatsAppOtpRequest(BaseModel):
    phone_number: str = Field(min_length=8, max_length=20)
    display_name: str | None = Field(default=None, max_length=100)

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        return normalize_e164_in(value)

    @field_validator("display_name")
    @classmethod
    def trim_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class WhatsAppOtpRequestResponse(BaseModel):
    message: str
    expires_in_seconds: int
    session_id: str
    debug_otp: str | None = None


class WhatsAppOtpVerifyRequest(BaseModel):
    phone_number: str = Field(min_length=8, max_length=20)
    otp: str = Field(pattern=r"^\d{6}$")
    session_id: str = Field(min_length=8, max_length=128)

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        return normalize_e164_in(value)


class WhatsAppOtpVerifyResponse(BaseModel):
    verified: bool
    phone_number: str
    message: str = "Phone verified"


def normalize_e164_in(value: str) -> str:
    """Normalize Indian / E.164 phones to +91XXXXXXXXXX when possible."""
    raw = value.strip()
    if not raw:
        raise ValueError("phone_number is required")

    if raw.startswith("+"):
        digits = _PHONE_DIGITS.sub("", raw[1:])
        candidate = f"+{digits}"
    else:
        digits = _PHONE_DIGITS.sub("", raw)
        if len(digits) == 10 and digits[0] in "6789":
            candidate = f"+91{digits}"
        elif digits.startswith("91") and len(digits) == 12:
            candidate = f"+{digits}"
        else:
            candidate = f"+{digits}"

    if not re.fullmatch(r"\+[1-9]\d{7,14}", candidate):
        raise ValueError("Invalid phone number. Use E.164, e.g. +9198XXXXXXXX.")
    return candidate


def require_whatsapp_configuration() -> None:
    if WHATSAPP_OTP_DEBUG:
        return
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "WhatsApp OTP is not configured. Set WHATSAPP_TOKEN and "
                "WHATSAPP_PHONE_NUMBER_ID, or WHATSAPP_OTP_DEBUG=true for local testing."
            ),
        )


def _hash_otp(salt: str, code: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode("utf-8")).hexdigest()


def _graph_messages_url() -> str:
    return f"https://graph.facebook.com/{WHATSAPP_GRAPH_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"


def _template_payload(to_e164: str, code: str) -> dict:
    components: list[dict] = [
        {
            "type": "body",
            "parameters": [{"type": "text", "text": code}],
        }
    ]
    if WHATSAPP_OTP_INCLUDE_BUTTON:
        components.append(
            {
                "type": "button",
                "sub_type": "url",
                "index": "0",
                "parameters": [{"type": "text", "text": code}],
            }
        )

    # Meta expects digits without leading +
    to = to_e164[1:] if to_e164.startswith("+") else to_e164
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "template",
        "template": {
            "name": WHATSAPP_OTP_TEMPLATE_NAME,
            "language": {"code": WHATSAPP_OTP_TEMPLATE_LANGUAGE},
            "components": components,
        },
    }


def send_whatsapp_otp(phone_e164: str, code: str) -> None:
    if WHATSAPP_OTP_DEBUG:
        return

    try:
        response = httpx.post(
            _graph_messages_url(),
            headers={
                "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                "Content-Type": "application/json",
            },
            json=_template_payload(phone_e164, code),
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to reach WhatsApp Cloud API") from exc

    if response.is_error:
        detail = "WhatsApp OTP send failed"
        try:
            error = response.json().get("error") or {}
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                detail = message.strip()
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=detail)


@router.post("/request", response_model=WhatsAppOtpRequestResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def request_whatsapp_otp(request: Request, payload: WhatsAppOtpRequest) -> WhatsAppOtpRequestResponse:
    require_whatsapp_configuration()

    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_hex(16)
    session_id = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=OTP_EXPIRE_MINUTES)

    send_whatsapp_otp(payload.phone_number, code)

    whatsapp_otp_requests.create_index("expires_at", expireAfterSeconds=0)
    whatsapp_otp_requests.update_one(
        {"phone_number": payload.phone_number},
        {
            "$set": {
                "session_id": session_id,
                "code_hash": _hash_otp(salt, code),
                "salt": salt,
                "display_name": payload.display_name,
                "attempts": 0,
                "created_at": now,
                "expires_at": expires_at,
            }
        },
        upsert=True,
    )

    return WhatsAppOtpRequestResponse(
        message="OTP sent on WhatsApp" if not WHATSAPP_OTP_DEBUG else "OTP generated (debug — not sent on WhatsApp)",
        expires_in_seconds=OTP_EXPIRE_MINUTES * 60,
        session_id=session_id,
        debug_otp=code if WHATSAPP_OTP_DEBUG else None,
    )


@router.post("/verify", response_model=WhatsAppOtpVerifyResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def verify_whatsapp_otp(request: Request, payload: WhatsAppOtpVerifyRequest) -> WhatsAppOtpVerifyResponse:
    now = datetime.now(timezone.utc)
    record = whatsapp_otp_requests.find_one(
        {
            "phone_number": payload.phone_number,
            "session_id": payload.session_id,
            "expires_at": {"$gt": now},
        }
    )
    if record is None:
        raise HTTPException(status_code=401, detail="Invalid or expired OTP session")

    attempts = int(record.get("attempts") or 0)
    if attempts >= OTP_MAX_ATTEMPTS:
        whatsapp_otp_requests.delete_one({"_id": record["_id"]})
        raise HTTPException(status_code=429, detail="Too many invalid OTP attempts. Request a new code.")

    expected = record.get("code_hash")
    salt = record.get("salt") or ""
    if not expected or not hmac_compare(expected, _hash_otp(salt, payload.otp)):
        whatsapp_otp_requests.update_one({"_id": record["_id"]}, {"$inc": {"attempts": 1}})
        raise HTTPException(status_code=401, detail="Invalid OTP")

    whatsapp_otp_requests.delete_one({"_id": record["_id"]})
    return WhatsAppOtpVerifyResponse(verified=True, phone_number=payload.phone_number)


def hmac_compare(left: str, right: str) -> bool:
    return secrets.compare_digest(left, right)
