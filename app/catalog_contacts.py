"""Verified junction.today shopper contacts (phone + email) and their orders.

Created/updated when catalog SMS OTP succeeds or when a junction.today order
is placed with contact fields. Returning shoppers unlock with MPIN after an
initial OTP setup (forgot MPIN re-runs OTP).
"""

import hashlib
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from pymongo import ReturnDocument

from .catalog_otp import normalize_e164_in
from .database import catalog_contacts, catalog_otp_requests, orders
from .login import (
    GCP_IDENTITY_PLATFORM_API_KEY,
    GCP_VERIFY_OTP_URL,
    gcp_error,
    hash_password,
    require_gcp_otp_configuration,
    verify_password,
)
from .rate_limit import RATE_LIMIT_AUTH, limiter

router = APIRouter(prefix="/auth/catalog-contacts", tags=["catalog-contacts"])


class CatalogContactUpsert(BaseModel):
    phone_number: str = Field(min_length=8, max_length=20)
    email: EmailStr
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


class CatalogContactRecognizeRequest(BaseModel):
    phone_number: str = Field(min_length=8, max_length=20)
    email: EmailStr

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        return normalize_e164_in(value)


class CatalogContactSummary(BaseModel):
    phone_number: str
    email: str
    display_name: str | None = None
    verified: bool = False
    order_count: int = 0
    recognized: bool = False
    has_mpin: bool = False


class CatalogContactOrder(BaseModel):
    id: str
    order_number: str
    store_id: str
    customer_name: str
    customer_phone: str | None = None
    customer_email: str | None = None
    total_amount: float
    currency: str
    status: str
    created_at: datetime


MPIN_PATTERN = r"^\d{4,6}$"


class CatalogMpinLoginRequest(BaseModel):
    phone_number: str = Field(min_length=8, max_length=20)
    # Optional — returning unlock is phone + MPIN; email was collected at account create.
    email: EmailStr | None = None
    mpin: str = Field(pattern=MPIN_PATTERN)

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        return normalize_e164_in(value)


class CatalogMpinSetupRequest(BaseModel):
    phone_number: str = Field(min_length=8, max_length=20)
    email: EmailStr
    otp: str = Field(pattern=r"^\d{6}$")
    session_info: str = Field(min_length=1)
    mpin: str = Field(pattern=MPIN_PATTERN)
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


class CatalogMpinAuthResponse(BaseModel):
    verified: bool
    phone_number: str
    email: str
    has_mpin: bool = True
    message: str = "Authenticated"


def upsert_catalog_contact(
    *,
    phone_number: str,
    email: str,
    display_name: str | None = None,
    verified: bool = False,
    order_id: str | None = None,
) -> dict:
    """Create or update a catalog contact keyed by E.164 phone."""
    now = datetime.now(timezone.utc)
    email_norm = email.strip().lower()
    phone = normalize_e164_in(phone_number)

    # Do not put `order_ids` in both $setOnInsert and $addToSet — Mongo rejects that path conflict.
    set_on_insert: dict = {
        "phone_number": phone,
        "created_at": now,
    }
    update: dict = {
        "$set": {
            "email": email_norm,
            "updated_at": now,
        },
        "$setOnInsert": set_on_insert,
    }
    if display_name:
        update["$set"]["display_name"] = display_name.strip()
    if verified:
        update["$set"]["verified"] = True
        update["$set"]["verified_at"] = now
    if order_id:
        update["$addToSet"] = {"order_ids": order_id}
    else:
        set_on_insert["order_ids"] = []

    catalog_contacts.create_index("phone_number", unique=True)
    catalog_contacts.update_one({"phone_number": phone}, update, upsert=True)
    return catalog_contacts.find_one({"phone_number": phone}) or {}


def find_verified_contact(phone_number: str, email: str) -> dict | None:
    phone = normalize_e164_in(phone_number)
    email_norm = email.strip().lower()
    doc = catalog_contacts.find_one({"phone_number": phone, "email": email_norm})
    if not doc or not doc.get("verified"):
        return None
    return doc


def find_verified_contact_by_phone(phone_number: str) -> dict | None:
    """Contacts are unique by phone — used for returning MPIN unlock."""
    phone = normalize_e164_in(phone_number)
    doc = catalog_contacts.find_one({"phone_number": phone})
    if not doc or not doc.get("verified"):
        return None
    return doc


@router.post("/recognize", response_model=CatalogContactSummary)
@limiter.limit(RATE_LIMIT_AUTH)
def recognize_catalog_contact(
    request: Request,
    payload: CatalogContactRecognizeRequest,
) -> CatalogContactSummary:
    """Match phone+email to a previously OTP-verified contact — skip fresh SMS."""
    doc = find_verified_contact(payload.phone_number, str(payload.email))
    if doc is None:
        return CatalogContactSummary(
            phone_number=payload.phone_number,
            email=str(payload.email).strip().lower(),
            verified=False,
            recognized=False,
            order_count=0,
            has_mpin=False,
        )

    order_ids = doc.get("order_ids") or []
    return CatalogContactSummary(
        phone_number=doc["phone_number"],
        email=doc.get("email") or str(payload.email).strip().lower(),
        display_name=doc.get("display_name"),
        verified=True,
        recognized=True,
        order_count=len(order_ids),
        has_mpin=bool((doc.get("mpin_hash") or "").strip()),
    )


@router.post("/mpin/login", response_model=CatalogMpinAuthResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def catalog_mpin_login(request: Request, payload: CatalogMpinLoginRequest) -> CatalogMpinAuthResponse:
    """Returning shopper unlock with phone + MPIN (email optional; stored on the contact)."""
    doc = find_verified_contact_by_phone(payload.phone_number)
    stored = (doc or {}).get("mpin_hash") or ""
    if doc is None or not stored or not verify_password(payload.mpin, stored):
        raise HTTPException(status_code=401, detail="Invalid phone or MPIN")
    email = (doc.get("email") or "").strip().lower()
    if payload.email is not None:
        # If a client still sends email, ignore mismatch — phone is the account key.
        email = email or str(payload.email).strip().lower()
    if not email:
        raise HTTPException(status_code=401, detail="Contact is missing email; reset with OTP")
    return CatalogMpinAuthResponse(
        verified=True,
        phone_number=doc["phone_number"],
        email=email,
        has_mpin=True,
        message="MPIN verified",
    )


@router.post("/mpin/setup", response_model=CatalogMpinAuthResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def catalog_mpin_setup(request: Request, payload: CatalogMpinSetupRequest) -> CatalogMpinAuthResponse:
    """After SMS OTP: set or reset MPIN for this phone+email contact."""
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
    upsert_catalog_contact(
        phone_number=payload.phone_number,
        email=str(payload.email),
        display_name=payload.display_name or stored.get("display_name"),
        verified=True,
    )
    email_norm = str(payload.email).strip().lower()
    doc = catalog_contacts.find_one_and_update(
        {"phone_number": payload.phone_number},
        {
            "$set": {
                "email": email_norm,
                "mpin_hash": hash_password(payload.mpin),
                "mpin_set_at": now,
                "verified": True,
                "verified_at": now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise HTTPException(status_code=500, detail="Could not save contact MPIN")
    return CatalogMpinAuthResponse(
        verified=True,
        phone_number=doc["phone_number"],
        email=email_norm,
        has_mpin=True,
        message="MPIN saved",
    )


@router.post("/orders", response_model=list[CatalogContactOrder])
@limiter.limit(RATE_LIMIT_AUTH)
def list_catalog_contact_orders(
    request: Request,
    payload: CatalogContactRecognizeRequest,
) -> list[CatalogContactOrder]:
    """Orders for a verified contact (phone + email must match stored profile)."""
    doc = find_verified_contact(payload.phone_number, str(payload.email))
    if doc is None:
        raise HTTPException(status_code=404, detail="No verified contact for this phone and email")

    phone = doc["phone_number"]
    email_norm = (doc.get("email") or "").strip().lower()
    documents = list(
        orders.find({"customer_phone": phone, "source": "junction.today"}).sort("created_at", -1).limit(50)
    )
    results: list[CatalogContactOrder] = []
    for document in documents:
        order_email = (document.get("customer_email") or "").strip().lower()
        if order_email and email_norm and order_email != email_norm:
            continue
        billing = document.get("billing") or {}
        results.append(
            CatalogContactOrder(
                id=str(document["_id"]),
                order_number=document.get("order_number") or "",
                store_id=document.get("store_id") or "",
                customer_name=document.get("customer_name") or "",
                customer_phone=document.get("customer_phone"),
                customer_email=document.get("customer_email"),
                total_amount=float(billing.get("total_amount") or 0),
                currency=str(billing.get("currency") or "INR"),
                status=str(document.get("status") or "pending"),
                created_at=document["created_at"],
            )
        )
    return results
