"""Simple Aadhaar verification (checksum + last-4 storage).

Replaces DigiLocker OAuth for profile completeness. We validate the 12-digit
number with the Verhoeff algorithm, store only the last 4 digits, and never
persist the full Aadhaar.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from .access_control import ensure_shop_access, get_shop_by_store_id
from .database import shops, users
from .login import get_current_user

router = APIRouter(prefix="/aadhaar", tags=["aadhaar"])

_DIGITS_RE = re.compile(r"\D+")

# Verhoeff multiplication table, permutation table, and inverse.
_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def verhoeff_ok(number: str) -> bool:
    """Return True when ``number`` (digits only) passes Verhoeff checksum."""
    if not number.isdigit():
        return False
    check = 0
    for i, ch in enumerate(reversed(number)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[i % 8][int(ch)]]
    return check == 0


def normalize_aadhaar(raw: str) -> str:
    digits = _DIGITS_RE.sub("", (raw or "").strip())
    if len(digits) != 12:
        raise ValueError("Aadhaar must be exactly 12 digits")
    if digits[0] in "01":
        raise ValueError("Aadhaar cannot start with 0 or 1")
    if not verhoeff_ok(digits):
        raise ValueError("Aadhaar checksum is invalid")
    return digits


class AadhaarVerifyRequest(BaseModel):
    aadhaar_number: str = Field(min_length=12, max_length=20)
    name: str | None = Field(default=None, max_length=120)
    shop_id: str | None = Field(default=None, max_length=64)

    @field_validator("aadhaar_number")
    @classmethod
    def validate_aadhaar(cls, value: str) -> str:
        return normalize_aadhaar(value)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class AadhaarVerifyResponse(BaseModel):
    aadhaar_verified: bool
    aadhaar_last4: str
    aadhaar_name: str | None = None
    message: str
    shop_id: str | None = None


@router.post("/verify", response_model=AadhaarVerifyResponse)
def verify_aadhaar(
    payload: AadhaarVerifyRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> AadhaarVerifyResponse:
    """Checksum-verify Aadhaar and store last-4 on the active shop (or user)."""
    digits = payload.aadhaar_number
    last4 = digits[-4:]
    now = datetime.now(timezone.utc)
    name = payload.name or (current_user.get("display_name") or "").strip() or None

    aadhaar_set = {
        "aadhaar_verified": True,
        "aadhaar_last4": last4,
        "aadhaar_name": name,
        "aadhaar_verified_at": now,
        # Clear DigiLocker badge when switching to Aadhaar-only flow.
        "digilocker_verified": False,
        "updated_at": now,
    }

    shop_id: str | None = None
    if payload.shop_id and payload.shop_id.strip():
        shop = get_shop_by_store_id(payload.shop_id.strip())
        ensure_shop_access(current_user, shop)
        shops.update_one({"_id": shop["_id"]}, {"$set": aadhaar_set})
        shop_id = str(shop["_id"])
    else:
        users.update_one({"_id": current_user["_id"]}, {"$set": aadhaar_set})

    return AadhaarVerifyResponse(
        aadhaar_verified=True,
        aadhaar_last4=last4,
        aadhaar_name=name,
        message="Aadhaar verified. Only the last 4 digits are stored.",
        shop_id=shop_id,
    )
