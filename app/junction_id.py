"""Junction ID: one phone + MPIN for every Junction app.

Every app calls `/auth/id/lookup` first and only creates an account when the phone is
new. `identities` holds the only credential; the app stores (staff `users`,
`catalog_contacts`, `monster_profiles`, `blog_accounts`) keep app data and are matched
by phone. Until an app moves here, its old MPIN or PIN is adopted the first time the
person signs in through Junction ID.

Avoid `from __future__ import annotations`: with slowapi it makes body models look like query params (422).
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal, NoReturn

import jwt
from bson import ObjectId
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .catalog_contacts import MPIN_LOCK_MINUTES, MPIN_MAX_FAILURES, MPIN_PATTERN, contact_display_name
from .catalog_otp import confirm_catalog_otp, normalize_e164_in
from .database import blog_accounts, catalog_contacts, identities, monster_profiles, users
from .login import JWT_EXPIRE_MINUTES, JWT_SECRET, hash_password, verify_password
from .rate_limit import RATE_LIMIT_AUTH, limiter

router = APIRouter(prefix="/auth/id", tags=["junction-id"])

ID_TOKEN_SCOPE = "id"
APP_PATTERN = r"^(earth|today|website|monster|blog)$"

PinKind = Literal["mpin", "blog_pin"]
AppStore = Literal["staff", "contact", "monster", "blog"]

_indexes_ready = False


def _ensure_indexes() -> None:
    global _indexes_ready
    if _indexes_ready:
        return
    identities.create_index("phone_number", unique=True)
    _indexes_ready = True


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class PhoneBody(BaseModel):
    phone_number: str = Field(min_length=8, max_length=20)

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        return normalize_e164_in(value)


class AppBody(PhoneBody):
    app: str | None = Field(default=None, pattern=APP_PATTERN)


class LoginRequest(AppBody):
    mpin: str = Field(pattern=MPIN_PATTERN)


class CreateRequest(LoginRequest):
    display_name: str | None = Field(default=None, max_length=100)

    @field_validator("display_name")
    @classmethod
    def trim_name(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


class UpgradeRequest(AppBody):
    old_pin: str = Field(min_length=1, max_length=8)
    mpin: str = Field(pattern=MPIN_PATTERN)


class ResetRequest(CreateRequest):
    otp: str = Field(pattern=r"^\d{6}$")
    session_info: str = Field(min_length=1)


class LookupResponse(BaseModel):
    phone_number: str
    #: True → ask for the MPIN (or the old PIN when `pin_kind` is `blog_pin`); False → offer create.
    exists: bool
    pin_kind: PinKind | None = None


class IdentityAuthResponse(BaseModel):
    phone_number: str
    display_name: str | None = None
    verified: bool
    message: str
    access_token: str
    expires_in: int


class IdentityMe(BaseModel):
    id: str
    phone_number: str
    display_name: str | None = None
    verified: bool
    created_via: str | None = None
    apps: dict[AppStore, bool]


# Order decides which old secret is adopted when someone has different MPINs in different apps.
_LEGACY: tuple[tuple[AppStore, str], ...] = (
    ("staff", "mpin_hash"),
    ("contact", "mpin_hash"),
    ("monster", "mpin_hash"),
    ("blog", "pin_hash"),
)


def _legacy_collection(store: AppStore):
    return {"staff": users, "contact": catalog_contacts, "monster": monster_profiles, "blog": blog_accounts}[store]


def _legacy_accounts(phone: str) -> dict[AppStore, dict]:
    found: dict[AppStore, dict] = {}
    for store, _field in _LEGACY:
        doc = _legacy_collection(store).find_one({"phone_number": phone})
        if doc is not None:
            found[store] = doc
    return found


def _legacy_secret(store: AppStore, doc: dict) -> str:
    field = dict(_LEGACY)[store]
    return (doc.get(field) or "").strip()


def _legacy_with_secret(phone: str) -> dict[AppStore, dict]:
    """Old accounts someone can actually sign in to; a jtoday order contact without an MPIN doesn't count."""
    return {store: doc for store, doc in _legacy_accounts(phone).items() if _legacy_secret(store, doc)}


def _legacy_profile(store: AppStore, doc: dict) -> tuple[bool, str | None]:
    """(phone verified by SMS, display name) carried over when an old account is adopted."""
    if store == "staff":
        return True, doc.get("display_name")
    if store == "contact":
        return bool(doc.get("verified")), contact_display_name(doc)
    if store == "monster":
        return False, doc.get("name")
    return False, doc.get("display_name")


def _has_mpin(doc: dict | None) -> bool:
    return bool(doc and (doc.get("mpin_hash") or "").strip())


def _mpin_version(doc: dict) -> int:
    set_at = doc.get("mpin_set_at")
    return int(_aware(set_at).timestamp()) if isinstance(set_at, datetime) else 0


def _create_token(doc: dict) -> str:
    if len(JWT_SECRET) < 32:
        raise HTTPException(status_code=503, detail="JWT_SECRET must contain at least 32 characters")
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(doc["_id"]),
            "scope": ID_TOKEN_SCOPE,
            "pv": _mpin_version(doc),
            "iat": now,
            "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES),
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def _auth_response(doc: dict, message: str) -> IdentityAuthResponse:
    return IdentityAuthResponse(
        phone_number=doc["phone_number"],
        display_name=doc.get("display_name"),
        verified=bool(doc.get("verified")),
        message=message,
        access_token=_create_token(doc),
        expires_in=JWT_EXPIRE_MINUTES * 60,
    )


def get_identity(
    x_junction_id: Annotated[str | None, Header(alias="X-Junction-Id")] = None,
) -> dict:
    token = (x_junction_id or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token or len(JWT_SECRET) < 32:
        raise HTTPException(status_code=401, detail="Sign in with your Junction MPIN first")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Session expired. Sign in again") from exc
    if payload.get("scope") != ID_TOKEN_SCOPE or not ObjectId.is_valid(str(payload.get("sub") or "")):
        raise HTTPException(status_code=401, detail="Sign in with your Junction MPIN first")
    doc = identities.find_one({"_id": ObjectId(payload["sub"])})
    if not _has_mpin(doc) or payload.get("pv") != _mpin_version(doc):
        raise HTTPException(status_code=401, detail="Session expired. Sign in again")
    return doc


IdentityAuth = Annotated[dict, Depends(get_identity)]


def _raise_if_locked(doc: dict | None) -> None:
    locked_until = (doc or {}).get("mpin_locked_until")
    now = datetime.now(timezone.utc)
    if isinstance(locked_until, datetime) and _aware(locked_until) > now:
        minutes = max(1, -(-int((_aware(locked_until) - now).total_seconds()) // 60))
        raise HTTPException(status_code=429, detail=f"Too many wrong MPINs. Try again in {minutes} min.")


def _record_failure(phone: str) -> NoReturn:
    """Counts a wrong secret against the phone (even before it has an identity) and locks at the limit."""
    now = datetime.now(timezone.utc)
    updated = identities.find_one_and_update(
        {"phone_number": phone},
        {"$inc": {"mpin_failed_count": 1}, "$setOnInsert": {"created_at": now}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if (updated or {}).get("mpin_failed_count", 0) >= MPIN_MAX_FAILURES:
        identities.update_one(
            {"_id": updated["_id"]},
            {
                "$set": {"mpin_locked_until": now + timedelta(minutes=MPIN_LOCK_MINUTES)},
                "$unset": {"mpin_failed_count": ""},
            },
        )
        raise HTTPException(status_code=429, detail=f"Too many wrong MPINs. Try again in {MPIN_LOCK_MINUTES} min.")
    raise HTTPException(status_code=401, detail="Invalid phone or MPIN")


def _save_mpin(phone: str, mpin: str, *, source: str, app: str | None, verified: bool, display_name: str | None) -> dict:
    now = datetime.now(timezone.utc)
    fields: dict = {
        "mpin_hash": hash_password(mpin),
        "mpin_set_at": now,
        "mpin_source": source,
        "updated_at": now,
    }
    if verified:
        fields.update(verified=True, verified_at=now)
    on_insert: dict = {"created_at": now, "created_via": app}
    if not verified:
        on_insert["verified"] = False
    doc = identities.find_one_and_update(
        {"phone_number": phone},
        {"$set": fields, "$setOnInsert": on_insert, "$unset": {"mpin_failed_count": "", "mpin_locked_until": ""}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if display_name and not doc.get("display_name"):
        doc = identities.find_one_and_update(
            {"_id": doc["_id"]}, {"$set": {"display_name": display_name}}, return_document=ReturnDocument.AFTER
        )
    return doc


def _adopt(phone: str, secret: str, legacy: dict[AppStore, dict], *, mpin: str, app: str | None) -> dict | None:
    """First old account whose secret matches becomes the Junction ID, with `mpin` as its MPIN."""
    for store, doc in legacy.items():
        if verify_password(secret, _legacy_secret(store, doc)):
            verified, name = _legacy_profile(store, doc)
            return _save_mpin(phone, mpin, source=f"adopted:{store}", app=app, verified=verified, display_name=name)
    return None


@router.post("/lookup", response_model=LookupResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def lookup(request: Request, payload: PhoneBody) -> LookupResponse:
    """The “check first” step: does this phone already have a Junction account anywhere?"""
    phone = payload.phone_number
    if _has_mpin(identities.find_one({"phone_number": phone})):
        return LookupResponse(phone_number=phone, exists=True, pin_kind="mpin")
    stores = set(_legacy_with_secret(phone))
    if stores - {"blog"}:
        return LookupResponse(phone_number=phone, exists=True, pin_kind="mpin")
    if stores:
        return LookupResponse(phone_number=phone, exists=True, pin_kind="blog_pin")
    return LookupResponse(phone_number=phone, exists=False)


@router.post("/login", response_model=IdentityAuthResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def login(request: Request, payload: LoginRequest) -> IdentityAuthResponse:
    _ensure_indexes()
    phone = payload.phone_number
    doc = identities.find_one({"phone_number": phone})
    _raise_if_locked(doc)
    if _has_mpin(doc):
        if verify_password(payload.mpin, doc["mpin_hash"]):
            if doc.get("mpin_failed_count") or doc.get("mpin_locked_until"):
                identities.update_one({"_id": doc["_id"]}, {"$unset": {"mpin_failed_count": "", "mpin_locked_until": ""}})
            return _auth_response(doc, "Signed in")
        _record_failure(phone)
    legacy = _legacy_with_secret(phone)
    if not legacy:
        raise HTTPException(status_code=401, detail="Invalid phone or MPIN")
    adopted = _adopt(phone, payload.mpin, legacy, mpin=payload.mpin, app=payload.app)
    if adopted is None:
        _record_failure(phone)
    return _auth_response(adopted, "Signed in")


@router.post("/create", response_model=IdentityAuthResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_AUTH)
def create(request: Request, payload: CreateRequest) -> IdentityAuthResponse:
    """New phone only: an existing Junction account anywhere means sign in instead."""
    _ensure_indexes()
    phone = payload.phone_number
    if _has_mpin(identities.find_one({"phone_number": phone})) or _legacy_with_secret(phone):
        raise HTTPException(status_code=409, detail="This number already has a Junction account. Sign in instead.")
    now = datetime.now(timezone.utc)
    try:
        doc = identities.find_one_and_update(
            {"phone_number": phone, "mpin_hash": {"$in": [None, ""]}},
            {
                "$set": {
                    "mpin_hash": hash_password(payload.mpin),
                    "mpin_set_at": now,
                    "mpin_source": "self",
                    "display_name": payload.display_name,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now, "created_via": payload.app, "verified": False},
                "$unset": {"mpin_failed_count": "", "mpin_locked_until": ""},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="This number already has a Junction account. Sign in instead.") from exc
    return _auth_response(doc, "Junction ID created")


@router.post("/upgrade", response_model=IdentityAuthResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def upgrade(request: Request, payload: UpgradeRequest) -> IdentityAuthResponse:
    """Old app PIN that is not an MPIN (the blog keypad): prove it once, then choose a Junction MPIN."""
    _ensure_indexes()
    phone = payload.phone_number
    doc = identities.find_one({"phone_number": phone})
    _raise_if_locked(doc)
    if _has_mpin(doc):
        raise HTTPException(status_code=409, detail="You already have a Junction MPIN. Sign in with it, or reset it by SMS.")
    legacy = _legacy_with_secret(phone)
    if not legacy:
        raise HTTPException(status_code=401, detail="Invalid phone or PIN")
    adopted = _adopt(phone, payload.old_pin, legacy, mpin=payload.mpin, app=payload.app)
    if adopted is None:
        _record_failure(phone)
    return _auth_response(adopted, "Junction MPIN set")


@router.post("/reset", response_model=IdentityAuthResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def reset(request: Request, payload: ResetRequest) -> IdentityAuthResponse:
    """Forgot MPIN: an SMS code from `/auth/catalog-otp/request` sets a new MPIN and marks the phone verified."""
    _ensure_indexes()
    phone = payload.phone_number
    stored = confirm_catalog_otp(phone, payload.session_info, payload.otp)
    name = payload.display_name
    if not name and stored.get("display_name") != "Junction customer":
        name = stored.get("display_name")
    doc = _save_mpin(phone, payload.mpin, source="sms", app=payload.app, verified=True, display_name=name)
    return _auth_response(doc, "MPIN saved")


@router.get("/me", response_model=IdentityMe)
@limiter.limit(RATE_LIMIT_AUTH)
def me(request: Request, identity: IdentityAuth) -> IdentityMe:
    """Who is signed in, and which Junction apps already know this phone (so they skip registration)."""
    legacy = _legacy_accounts(identity["phone_number"])
    return IdentityMe(
        id=str(identity["_id"]),
        phone_number=identity["phone_number"],
        display_name=identity.get("display_name"),
        verified=bool(identity.get("verified")),
        created_via=identity.get("created_via"),
        apps={store: store in legacy for store, _field in _LEGACY},
    )
