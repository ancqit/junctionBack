"""junctionBlog phone + 4-character PIN lock (character-map PIN).

No traditional register: phone is the account key. New users set a 4-char PIN
from a fixed charset; returning users unlock with the same map.

Avoid `from __future__ import annotations`: with slowapi it makes body models look like query params (422).
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from pymongo import ReturnDocument

from .database import blog_accounts
from .login import JWT_EXPIRE_MINUTES, JWT_SECRET, hash_password, verify_password
from .rate_limit import RATE_LIMIT_AUTH, limiter

router = APIRouter(prefix="/blog/auth", tags=["blog-auth"])
bearer_scheme = HTTPBearer(auto_error=False)

PIN_LENGTH = 4
# Fixed character map shown full-screen for setup / unlock / update.
BLOG_PIN_CHARSET = list(
    "ABCDEFGHJKLMNPQRSTUVWXYZ"
    "23456789"
    "@#$%&*+="
    "★◆●▲■♥☀⚡"
)

_PHONE_DIGITS = re.compile(r"\D+")


class CharsetResponse(BaseModel):
    characters: list[str]
    pin_length: int = PIN_LENGTH


class BlogPinSetupRequest(BaseModel):
    phone_number: str = Field(min_length=8, max_length=20)
    pin: str = Field(min_length=PIN_LENGTH, max_length=PIN_LENGTH)
    display_name: str | None = Field(default=None, max_length=100)

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        return normalize_e164_in(value)

    @field_validator("pin")
    @classmethod
    def validate_pin(cls, value: str) -> str:
        return require_valid_pin(value)

    @field_validator("display_name")
    @classmethod
    def trim_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class BlogPinLoginRequest(BaseModel):
    phone_number: str = Field(min_length=8, max_length=20)
    pin: str = Field(min_length=PIN_LENGTH, max_length=PIN_LENGTH)

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        return normalize_e164_in(value)

    @field_validator("pin")
    @classmethod
    def validate_pin(cls, value: str) -> str:
        return require_valid_pin(value)


class BlogPinUpdateRequest(BaseModel):
    current_pin: str = Field(min_length=PIN_LENGTH, max_length=PIN_LENGTH)
    new_pin: str = Field(min_length=PIN_LENGTH, max_length=PIN_LENGTH)

    @field_validator("current_pin", "new_pin")
    @classmethod
    def validate_pin(cls, value: str) -> str:
        return require_valid_pin(value)


class BlogAuthUser(BaseModel):
    id: str
    phone_number: str
    display_name: str
    user_number: str


class BlogTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: BlogAuthUser


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


def require_valid_pin(value: str) -> str:
    if len(value) != PIN_LENGTH:
        raise ValueError(f"PIN must be exactly {PIN_LENGTH} characters")
    allowed = set(BLOG_PIN_CHARSET)
    if any(ch not in allowed for ch in value):
        raise ValueError("PIN characters must come from the character map")
    return value


def _secret() -> str:
    if len(JWT_SECRET) < 32:
        raise HTTPException(status_code=503, detail="JWT_SECRET must contain at least 32 characters")
    return JWT_SECRET


def create_blog_access_token(account_id: ObjectId) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(account_id),
            "scope": "blog",
            "iat": now,
            "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES),
        },
        _secret(),
        algorithm="HS256",
    )


def user_number_for_phone(phone: str) -> str:
    digits = _PHONE_DIGITS.sub("", phone)
    return digits[-4:] if len(digits) >= 4 else digits or "0000"


def serialize_account(document: dict) -> BlogAuthUser:
    return BlogAuthUser(
        id=str(document["_id"]),
        phone_number=document["phone_number"],
        display_name=document.get("display_name") or "Blogger",
        user_number=document.get("user_number") or user_number_for_phone(document["phone_number"]),
    )


def token_response(document: dict) -> BlogTokenResponse:
    return BlogTokenResponse(
        access_token=create_blog_access_token(document["_id"]),
        user=serialize_account(document),
    )


def get_blog_account(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> dict:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(credentials.credentials, _secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc
    if payload.get("scope") != "blog":
        raise HTTPException(status_code=401, detail="Blog lock token required")
    account_id = payload.get("sub")
    if not account_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        oid = ObjectId(account_id)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    document = blog_accounts.find_one({"_id": oid})
    if document is None:
        raise HTTPException(status_code=401, detail="Account not found")
    return document


@router.get("/charset", response_model=CharsetResponse)
def get_charset() -> CharsetResponse:
    return CharsetResponse(characters=list(BLOG_PIN_CHARSET), pin_length=PIN_LENGTH)


@router.post("/setup", response_model=BlogTokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_AUTH)
def setup_pin(request: Request, payload: BlogPinSetupRequest) -> BlogTokenResponse:
    """New user (or first-time PIN): create blog account with character-map PIN."""
    existing = blog_accounts.find_one({"phone_number": payload.phone_number})
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="This number already has a PIN. Use Old user → unlock, or update PIN from profile.",
        )

    now = datetime.now(timezone.utc)
    display_name = payload.display_name or f"User {user_number_for_phone(payload.phone_number)}"
    document = {
        "phone_number": payload.phone_number,
        "display_name": display_name,
        "user_number": user_number_for_phone(payload.phone_number),
        "pin_hash": hash_password(payload.pin),
        "created_at": now,
        "updated_at": now,
    }
    blog_accounts.create_index("phone_number", unique=True)
    result = blog_accounts.insert_one(document)
    document["_id"] = result.inserted_id
    return token_response(document)


@router.post("/login", response_model=BlogTokenResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def login_pin(request: Request, payload: BlogPinLoginRequest) -> BlogTokenResponse:
    """Old user: unlock with phone + 4 character-map PIN."""
    document = blog_accounts.find_one({"phone_number": payload.phone_number})
    if document is None or not verify_password(payload.pin, document.get("pin_hash") or ""):
        raise HTTPException(status_code=401, detail="Invalid phone or PIN")
    return token_response(document)


@router.post("/update-pin", response_model=BlogTokenResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def update_pin(
    request: Request,
    payload: BlogPinUpdateRequest,
    account: Annotated[dict, Depends(get_blog_account)],
) -> BlogTokenResponse:
    if not verify_password(payload.current_pin, account.get("pin_hash") or ""):
        raise HTTPException(status_code=401, detail="Current PIN is incorrect")
    if payload.current_pin == payload.new_pin:
        raise HTTPException(status_code=400, detail="New PIN must be different")

    now = datetime.now(timezone.utc)
    updated = blog_accounts.find_one_and_update(
        {"_id": account["_id"]},
        {"$set": {"pin_hash": hash_password(payload.new_pin), "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    return token_response(updated or account)


@router.get("/me", response_model=BlogAuthUser)
def me(account: Annotated[dict, Depends(get_blog_account)]) -> BlogAuthUser:
    return serialize_account(account)
