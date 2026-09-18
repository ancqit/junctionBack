"""junction.monster creator profiles — phone + MPIN lock, unique profile name.

Flow:
1. Lookup phone → existing profile or not
2. New: set display name, profile_name (availability), MPIN
3. Returning: unlock with MPIN
4. Authenticated creators own shorts (list + delete)

Avoid `from __future__ import annotations`: with slowapi it makes body models look like query params (422).
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from bson import ObjectId
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from pymongo import ReturnDocument

from .database import monster_posts, monster_profiles
from .login import JWT_EXPIRE_MINUTES, JWT_SECRET, hash_password, verify_password
from .rate_limit import RATE_LIMIT_AUTH, RATE_LIMIT_CATALOG, limiter

router = APIRouter(prefix="/monster/profiles", tags=["monster-profiles"])
bearer_scheme = HTTPBearer(auto_error=False)

MPIN_PATTERN = r"^\d{4,6}$"
PROFILE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,29}$")
_PHONE_DIGITS = re.compile(r"\D+")
MONSTER_PROFILE_HEADER = "X-Monster-Profile"


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


def normalize_profile_name(value: str) -> str:
    handle = value.strip().lower().lstrip("@")
    if not PROFILE_NAME_PATTERN.fullmatch(handle):
        raise ValueError(
            "Profile name must be 3–30 chars: start with a letter, then letters, numbers, or _"
        )
    return handle


def _secret() -> str:
    if len(JWT_SECRET) < 32:
        raise HTTPException(status_code=503, detail="JWT_SECRET must contain at least 32 characters")
    return JWT_SECRET


def _ensure_indexes() -> None:
    monster_profiles.create_index("phone_number", unique=True)
    monster_profiles.create_index("profile_name", unique=True)
    monster_posts.create_index([("profile_id", 1), ("created_at", -1)])


class PhoneLookupRequest(BaseModel):
    phone_number: str = Field(min_length=8, max_length=20)

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        return normalize_e164_in(value)


class PhoneLookupResponse(BaseModel):
    phone_number: str
    exists: bool
    has_mpin: bool = False
    profile_name: str | None = None
    name: str | None = None


class ProfileNameCheckResponse(BaseModel):
    profile_name: str
    available: bool


class ProfileRegisterRequest(BaseModel):
    phone_number: str = Field(min_length=8, max_length=20)
    name: str = Field(min_length=2, max_length=60)
    profile_name: str = Field(min_length=3, max_length=30)
    mpin: str = Field(pattern=MPIN_PATTERN)

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        return normalize_e164_in(value)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        trimmed = value.strip()
        if len(trimmed) < 2:
            raise ValueError("Name must be at least 2 characters")
        return trimmed

    @field_validator("profile_name")
    @classmethod
    def normalize_handle(cls, value: str) -> str:
        return normalize_profile_name(value)


class ProfileLoginRequest(BaseModel):
    phone_number: str = Field(min_length=8, max_length=20)
    mpin: str = Field(pattern=MPIN_PATTERN)

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        return normalize_e164_in(value)


class MonsterProfileUser(BaseModel):
    id: str
    phone_number: str
    name: str
    profile_name: str


class MonsterProfileTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: MonsterProfileUser


def create_monster_access_token(profile_id: ObjectId) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(profile_id),
            "scope": "monster",
            "iat": now,
            "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES),
        },
        _secret(),
        algorithm="HS256",
    )


def serialize_profile(document: dict) -> MonsterProfileUser:
    return MonsterProfileUser(
        id=str(document["_id"]),
        phone_number=document["phone_number"],
        name=document.get("name") or "",
        profile_name=document.get("profile_name") or "",
    )


def token_response(document: dict) -> MonsterProfileTokenResponse:
    return MonsterProfileTokenResponse(
        access_token=create_monster_access_token(document["_id"]),
        expires_in=JWT_EXPIRE_MINUTES * 60,
        user=serialize_profile(document),
    )


def _token_from_headers(
    credentials: HTTPAuthorizationCredentials | None,
    x_monster_profile: str | None,
) -> str | None:
    """Prefer X-Monster-Profile so session Bearer can coexist on create/upload."""
    if x_monster_profile and x_monster_profile.strip():
        raw = x_monster_profile.strip()
        if raw.lower().startswith("bearer "):
            return raw[7:].strip()
        return raw
    if credentials is not None and credentials.scheme.lower() == "bearer":
        return credentials.credentials
    return None


def _load_profile_from_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, _secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc
    if payload.get("scope") != "monster":
        raise HTTPException(status_code=401, detail="Monster profile token required")
    account_id = payload.get("sub")
    if not account_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        oid = ObjectId(account_id)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    document = monster_profiles.find_one({"_id": oid})
    if document is None:
        raise HTTPException(status_code=401, detail="Profile not found")
    return document


def get_monster_profile(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    x_monster_profile: Annotated[str | None, Header(alias="X-Monster-Profile")] = None,
) -> dict:
    token = _token_from_headers(credentials, x_monster_profile)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _load_profile_from_token(token)


def get_optional_monster_profile(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    x_monster_profile: Annotated[str | None, Header(alias="X-Monster-Profile")] = None,
) -> dict | None:
    token = _token_from_headers(credentials, x_monster_profile)
    if not token:
        return None
    try:
        return _load_profile_from_token(token)
    except HTTPException:
        return None


MonsterProfileAuth = Annotated[dict, Depends(get_monster_profile)]
OptionalMonsterProfile = Annotated[dict | None, Depends(get_optional_monster_profile)]


@router.post("/lookup", response_model=PhoneLookupResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def lookup_profile(request: Request, payload: PhoneLookupRequest) -> PhoneLookupResponse:
    """Step 1 — check whether this phone already has a monster profile."""
    _ = request
    _ensure_indexes()
    doc = monster_profiles.find_one({"phone_number": payload.phone_number})
    if doc is None:
        return PhoneLookupResponse(phone_number=payload.phone_number, exists=False)
    return PhoneLookupResponse(
        phone_number=doc["phone_number"],
        exists=True,
        has_mpin=bool((doc.get("mpin_hash") or "").strip()),
        profile_name=doc.get("profile_name"),
        name=doc.get("name"),
    )


@router.get("/check-name", response_model=ProfileNameCheckResponse)
@limiter.limit(RATE_LIMIT_CATALOG)
def check_profile_name(
    request: Request,
    profile_name: str = Query(min_length=3, max_length=30),
) -> ProfileNameCheckResponse:
    """Validate uniqueness for the public @profile_name handle."""
    _ = request
    _ensure_indexes()
    try:
        handle = normalize_profile_name(profile_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    taken = monster_profiles.find_one({"profile_name": handle}, {"_id": 1}) is not None
    return ProfileNameCheckResponse(profile_name=handle, available=not taken)


@router.post("/register", response_model=MonsterProfileTokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_AUTH)
def register_profile(request: Request, payload: ProfileRegisterRequest) -> MonsterProfileTokenResponse:
    """New creator — phone not in DB yet: set name, profile_name, MPIN."""
    _ = request
    _ensure_indexes()
    if monster_profiles.find_one({"phone_number": payload.phone_number}):
        raise HTTPException(
            status_code=409,
            detail="This number already has a profile. Unlock with your MPIN.",
        )
    if monster_profiles.find_one({"profile_name": payload.profile_name}):
        raise HTTPException(status_code=409, detail="Profile name is taken. Try another.")

    now = datetime.now(timezone.utc)
    document = {
        "phone_number": payload.phone_number,
        "name": payload.name,
        "profile_name": payload.profile_name,
        "mpin_hash": hash_password(payload.mpin),
        "mpin_set_at": now,
        "created_at": now,
        "updated_at": now,
    }
    result = monster_profiles.insert_one(document)
    document["_id"] = result.inserted_id
    return token_response(document)


@router.post("/login", response_model=MonsterProfileTokenResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def login_profile(request: Request, payload: ProfileLoginRequest) -> MonsterProfileTokenResponse:
    """Returning creator — unlock with phone + MPIN."""
    _ = request
    _ensure_indexes()
    document = monster_profiles.find_one({"phone_number": payload.phone_number})
    stored = (document or {}).get("mpin_hash") or ""
    if document is None or not stored or not verify_password(payload.mpin, stored):
        raise HTTPException(status_code=401, detail="Invalid phone or MPIN")
    return token_response(document)


@router.get("/me", response_model=MonsterProfileUser)
@limiter.limit(RATE_LIMIT_AUTH)
def get_me(request: Request, profile: MonsterProfileAuth) -> MonsterProfileUser:
    _ = request
    return serialize_profile(profile)


class CompleteSetupRequest(BaseModel):
    """When phone exists but profile_name / MPIN still need to be set (rare recovery)."""

    phone_number: str = Field(min_length=8, max_length=20)
    name: str = Field(min_length=2, max_length=60)
    profile_name: str = Field(min_length=3, max_length=30)
    mpin: str = Field(pattern=MPIN_PATTERN)

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        return normalize_e164_in(value)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        trimmed = value.strip()
        if len(trimmed) < 2:
            raise ValueError("Name must be at least 2 characters")
        return trimmed

    @field_validator("profile_name")
    @classmethod
    def normalize_handle(cls, value: str) -> str:
        return normalize_profile_name(value)


@router.post("/complete", response_model=MonsterProfileTokenResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def complete_profile_setup(request: Request, payload: CompleteSetupRequest) -> MonsterProfileTokenResponse:
    """Phone found without MPIN — set name, profile_name, and MPIN."""
    _ = request
    _ensure_indexes()
    document = monster_profiles.find_one({"phone_number": payload.phone_number})
    if document is None:
        raise HTTPException(status_code=404, detail="No profile for this number — register instead")
    if (document.get("mpin_hash") or "").strip():
        raise HTTPException(status_code=409, detail="MPIN already set — unlock with login")

    clash = monster_profiles.find_one(
        {"profile_name": payload.profile_name, "_id": {"$ne": document["_id"]}}
    )
    if clash is not None:
        raise HTTPException(status_code=409, detail="Profile name is taken. Try another.")

    now = datetime.now(timezone.utc)
    updated = monster_profiles.find_one_and_update(
        {"_id": document["_id"]},
        {
            "$set": {
                "name": payload.name,
                "profile_name": payload.profile_name,
                "mpin_hash": hash_password(payload.mpin),
                "mpin_set_at": now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="Could not complete profile")
    return token_response(updated)
