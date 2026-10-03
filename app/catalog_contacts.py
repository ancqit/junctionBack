"""Verified junction.today shopper contacts (phone + email) and their orders.

Created/updated when catalog SMS OTP succeeds or when a junction.today order
is placed with contact fields. Returning shoppers unlock with MPIN after an
initial OTP setup (forgot MPIN re-runs OTP).
"""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Annotated

import httpx
import jwt
from bson import ObjectId
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .catalog_otp import normalize_e164_in
from .database import catalog_contacts, catalog_otp_requests, orders
from .login import (
    GCP_IDENTITY_PLATFORM_API_KEY,
    GCP_VERIFY_OTP_URL,
    JWT_EXPIRE_MINUTES,
    JWT_SECRET,
    gcp_error,
    hash_password,
    require_gcp_otp_configuration,
    verify_password,
)
from .orders import new_bill_token
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
    bill_token: str | None = None


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


class CatalogMpinCreateRequest(BaseModel):
    """jEarth open sign-up: phone + MPIN, no email or SMS. Never overwrites an existing MPIN."""

    phone_number: str = Field(min_length=8, max_length=20)
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
    display_name: str | None = None
    # Customer token for MPIN-gated features (jEarth Home trash); sent back as X-Junction-Contact.
    access_token: str | None = None
    expires_in: int | None = None


CONTACT_TOKEN_SCOPE = "contact"
SELF_MPIN = "self"
MPIN_MAX_FAILURES = 5
MPIN_LOCK_MINUTES = 15


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _can_unlock(doc: dict) -> bool:
    """SMS-verified contacts, or phone + MPIN accounts made on jEarth without SMS."""
    return bool(doc.get("verified")) or doc.get("mpin_source") == SELF_MPIN


def contact_display_name(doc: dict) -> str | None:
    """Self-set accounts only ever see the name they typed, never one taken from an order."""
    if doc.get("verified"):
        return doc.get("display_name")
    return doc.get("earth_display_name")


def check_mpin(doc: dict, mpin: str) -> None:
    """Verify the MPIN; 5 wrong tries lock this phone for 15 minutes."""
    now = datetime.now(timezone.utc)
    locked_until = doc.get("mpin_locked_until")
    if isinstance(locked_until, datetime) and _aware(locked_until) > now:
        minutes = max(1, -(-int((_aware(locked_until) - now).total_seconds()) // 60))
        raise HTTPException(status_code=429, detail=f"Too many wrong MPINs. Try again in {minutes} min.")
    if verify_password(mpin, doc.get("mpin_hash") or ""):
        if doc.get("mpin_failed_count") or locked_until:
            catalog_contacts.update_one(
                {"_id": doc["_id"]}, {"$unset": {"mpin_failed_count": "", "mpin_locked_until": ""}}
            )
        return
    updated = catalog_contacts.find_one_and_update(
        {"_id": doc["_id"]},
        {"$inc": {"mpin_failed_count": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if (updated or {}).get("mpin_failed_count", 0) >= MPIN_MAX_FAILURES:
        catalog_contacts.update_one(
            {"_id": doc["_id"]},
            {
                "$set": {"mpin_locked_until": now + timedelta(minutes=MPIN_LOCK_MINUTES)},
                "$unset": {"mpin_failed_count": ""},
            },
        )
        raise HTTPException(
            status_code=429, detail=f"Too many wrong MPINs. Try again in {MPIN_LOCK_MINUTES} min."
        )
    raise HTTPException(status_code=401, detail="Invalid phone or MPIN")


def _mpin_version(doc: dict) -> int:
    set_at = doc.get("mpin_set_at")
    if not isinstance(set_at, datetime):
        return 0
    return int((set_at if set_at.tzinfo else set_at.replace(tzinfo=timezone.utc)).timestamp())


def create_contact_token(doc: dict) -> str | None:
    """Signed customer token; resetting the MPIN invalidates every earlier token."""
    if len(JWT_SECRET) < 32:
        return None
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(doc["_id"]),
            "scope": CONTACT_TOKEN_SCOPE,
            "pv": _mpin_version(doc),
            "iat": now,
            "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES),
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def get_contact(
    x_junction_contact: Annotated[str | None, Header(alias="X-Junction-Contact")] = None,
) -> dict:
    token = (x_junction_contact or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token or len(JWT_SECRET) < 32:
        raise HTTPException(status_code=401, detail="Unlock with your MPIN first")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Session expired. Unlock with your MPIN again") from exc
    if payload.get("scope") != CONTACT_TOKEN_SCOPE or not ObjectId.is_valid(str(payload.get("sub") or "")):
        raise HTTPException(status_code=401, detail="Unlock with your MPIN first")
    doc = catalog_contacts.find_one({"_id": ObjectId(payload["sub"])})
    if not doc or not _can_unlock(doc) or payload.get("pv") != _mpin_version(doc):
        raise HTTPException(status_code=401, detail="Session expired. Unlock with your MPIN again")
    return doc


ContactAuth = Annotated[dict, Depends(get_contact)]


def contact_auth_response(doc: dict, *, email: str, message: str) -> CatalogMpinAuthResponse:
    token = create_contact_token(doc)
    return CatalogMpinAuthResponse(
        verified=bool(doc.get("verified")),
        phone_number=doc["phone_number"],
        email=email,
        has_mpin=True,
        message=message,
        display_name=contact_display_name(doc),
        access_token=token,
        expires_in=JWT_EXPIRE_MINUTES * 60 if token else None,
    )


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
    if doc is None or not (doc.get("mpin_hash") or "").strip():
        raise HTTPException(status_code=401, detail="Invalid phone or MPIN")
    check_mpin(doc, payload.mpin)
    email = (doc.get("email") or "").strip().lower()
    if payload.email is not None:
        # If a client still sends email, ignore mismatch — phone is the account key.
        email = email or str(payload.email).strip().lower()
    if not email:
        raise HTTPException(status_code=401, detail="Contact is missing email; reset with OTP")
    return contact_auth_response(doc, email=email, message="MPIN verified")


@router.post("/mpin/unlock", response_model=CatalogMpinAuthResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def catalog_mpin_unlock(request: Request, payload: CatalogMpinLoginRequest) -> CatalogMpinAuthResponse:
    """jEarth unlock: junction.today MPINs and phone + MPIN accounts made on jEarth."""
    doc = catalog_contacts.find_one({"phone_number": payload.phone_number})
    if doc is None or not (doc.get("mpin_hash") or "").strip() or not _can_unlock(doc):
        raise HTTPException(status_code=401, detail="Invalid phone or MPIN")
    check_mpin(doc, payload.mpin)
    email = (doc.get("email") or "").strip().lower() if doc.get("verified") else ""
    return contact_auth_response(doc, email=email, message="MPIN verified")


@router.post("/mpin/create", response_model=CatalogMpinAuthResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def catalog_mpin_create(request: Request, payload: CatalogMpinCreateRequest) -> CatalogMpinAuthResponse:
    """Phone + MPIN sign-up without SMS. Numbers that already have an MPIN or a verified account must use SMS."""
    existing = catalog_contacts.find_one({"phone_number": payload.phone_number})
    if existing and ((existing.get("mpin_hash") or "").strip() or existing.get("verified")):
        raise HTTPException(
            status_code=409,
            detail="This number already has a Junction account. Unlock with your MPIN, or reset it with an SMS code.",
        )
    now = datetime.now(timezone.utc)
    fields: dict = {
        "mpin_hash": hash_password(payload.mpin),
        "mpin_set_at": now,
        "mpin_source": SELF_MPIN,
        "updated_at": now,
    }
    if payload.display_name:
        fields["earth_display_name"] = payload.display_name
    catalog_contacts.create_index("phone_number", unique=True)
    try:
        doc = catalog_contacts.find_one_and_update(
            {"phone_number": payload.phone_number, "verified": {"$ne": True}, "mpin_hash": {"$in": [None, ""]}},
            {"$set": fields, "$setOnInsert": {"created_at": now, "order_ids": []}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="This number already has a Junction account. Unlock with your MPIN, or reset it with an SMS code.",
        ) from exc
    return contact_auth_response(doc, email="", message="MPIN created")


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
            },
            "$unset": {"mpin_source": "", "mpin_failed_count": "", "mpin_locked_until": ""},
        },
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise HTTPException(status_code=500, detail="Could not save contact MPIN")
    return contact_auth_response(doc, email=email_norm, message="MPIN saved")


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
        bill_token = document.get("bill_token")
        if not bill_token:
            bill_token = new_bill_token()
            orders.update_one({"_id": document["_id"]}, {"$set": {"bill_token": bill_token}})
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
                bill_token=bill_token,
            )
        )
    return results
