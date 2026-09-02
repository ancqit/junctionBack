"""Catalog (junction.today) phone OTP via GCP Identity Platform SMS.

Same Google SMS path as owner login (`/auth/otp/*`), but verify only confirms
the phone — it does not create a Junction owner account or issue an owner JWT.

Avoid `from __future__ import annotations`: with slowapi it makes body models look like query params (422).
"""

import hashlib
import re
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from .database import catalog_otp_requests
from .login import (
    GCP_IDENTITY_PLATFORM_API_KEY,
    GCP_SEND_OTP_URL,
    GCP_VERIFY_OTP_URL,
    OTP_EXPIRE_MINUTES,
    OtpRequest,
    gcp_error,
    gcp_send_otp_payload,
    require_gcp_otp_configuration,
)
from .rate_limit import RATE_LIMIT_AUTH, limiter

router = APIRouter(prefix="/auth/catalog-otp", tags=["catalog-otp"])

_PHONE_DIGITS = re.compile(r"\D+")


class CatalogOtpRequest(BaseModel):
    """Web checkout OTP — same bot checks as owner login."""

    phone_number: str = Field(min_length=8, max_length=20)
    display_name: str | None = Field(default=None, max_length=100)
    recaptcha_token: str | None = None
    client_type: str | None = "web"

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

    @model_validator(mode="after")
    def require_recaptcha(self) -> "CatalogOtpRequest":
        if not (self.recaptcha_token or "").strip():
            raise ValueError("recaptcha_token is required")
        return self


class CatalogOtpRequestResponse(BaseModel):
    message: str
    expires_in_seconds: int
    session_info: str


class CatalogOtpVerifyRequest(BaseModel):
    phone_number: str = Field(min_length=8, max_length=20)
    otp: str = Field(pattern=r"^\d{6}$")
    session_info: str = Field(min_length=1)

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        return normalize_e164_in(value)


class CatalogOtpVerifyResponse(BaseModel):
    verified: bool
    phone_number: str
    message: str = "Phone verified"


def normalize_e164_in(value: str) -> str:
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


def _as_owner_otp_request(payload: CatalogOtpRequest) -> OtpRequest:
    return OtpRequest(
        display_name=(payload.display_name or "Junction customer").strip() or "Junction customer",
        phone_number=payload.phone_number,
        recaptcha_token=payload.recaptcha_token,
        client_type=payload.client_type or "web",
    )


@router.post("/request", response_model=CatalogOtpRequestResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def request_catalog_otp(request: Request, payload: CatalogOtpRequest) -> CatalogOtpRequestResponse:
    require_gcp_otp_configuration()
    owner_payload = _as_owner_otp_request(payload)
    try:
        response = httpx.post(
            GCP_SEND_OTP_URL,
            params={"key": GCP_IDENTITY_PLATFORM_API_KEY},
            json=gcp_send_otp_payload(owner_payload),
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to reach GCP Identity Platform") from exc
    if response.is_error:
        raise gcp_error(response, client_type=owner_payload.client_type)

    session_info = response.json().get("sessionInfo")
    if not session_info:
        raise HTTPException(status_code=502, detail="GCP did not return an OTP session")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=OTP_EXPIRE_MINUTES)
    catalog_otp_requests.create_index("expires_at", expireAfterSeconds=0)
    catalog_otp_requests.update_one(
        {"phone_number": payload.phone_number},
        {
            "$set": {
                "display_name": owner_payload.display_name,
                "session_hash": hashlib.sha256(session_info.encode()).hexdigest(),
                "created_at": now,
                "expires_at": expires_at,
            }
        },
        upsert=True,
    )
    return CatalogOtpRequestResponse(
        message="OTP sent by SMS",
        expires_in_seconds=OTP_EXPIRE_MINUTES * 60,
        session_info=session_info,
    )


@router.post("/verify", response_model=CatalogOtpVerifyResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def verify_catalog_otp(request: Request, payload: CatalogOtpVerifyRequest) -> CatalogOtpVerifyResponse:
    require_gcp_otp_configuration()
    now = datetime.now(timezone.utc)
    session_hash = hashlib.sha256(payload.session_info.encode()).hexdigest()
    stored = catalog_otp_requests.find_one(
        {
            "phone_number": payload.phone_number,
            "session_hash": session_hash,
            "expires_at": {"$gt": now},
        }
    )
    if stored is None:
        raise HTTPException(status_code=401, detail="Invalid or expired OTP session")

    try:
        response = httpx.post(
            GCP_VERIFY_OTP_URL,
            params={"key": GCP_IDENTITY_PLATFORM_API_KEY},
            json={"sessionInfo": payload.session_info, "code": payload.otp},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to reach GCP Identity Platform") from exc
    if response.is_error:
        raise gcp_error(response)

    verified_phone = response.json().get("phoneNumber")
    if verified_phone != payload.phone_number:
        raise HTTPException(status_code=401, detail="GCP phone verification did not match the request")

    catalog_otp_requests.delete_one({"_id": stored["_id"]})
    return CatalogOtpVerifyResponse(verified=True, phone_number=payload.phone_number)
