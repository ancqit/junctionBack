"""
Optional env-driven test owner for local / staging payment checks.

Set on Render (or .env):
  TEST_USER_PHONE=+9198XXXXXXXX
  TEST_USER_MPIN=123456
  TEST_USER_NAME=Payments Tester   # optional
  TEST_USER_EMAIL=tester@example.com  # optional

Optional shop so Plans/Payments has city+locality ready:
  TEST_SHOP_NAME=Payments Test Shop
  TEST_SHOP_CITY=Bengaluru
  TEST_SHOP_LOCALITY=Indiranagar

Seed runs once on API startup when phone + MPIN are both set.
MPIN is re-hashed from env on every boot so rotating the env var updates login.
Never commit real credentials — only placeholders in .env.example.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from .database import shops, users
from .login import hash_password, resolve_role_for_user
from .plan_service import initialize_user_plan, plan_document_for_new_shop, restore_persisted_plan
from .roles import UserRole

_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")
_MPIN_RE = re.compile(r"^\d{4,6}$")


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def ensure_test_user() -> dict | None:
    """
    Upsert the env-configured test owner (phone + MPIN).
    Returns the user document, or None when env is unset / invalid.
    """
    phone = _env("TEST_USER_PHONE")
    mpin = _env("TEST_USER_MPIN")
    if not phone and not mpin:
        return None
    if not phone or not mpin:
        print(
            "test user seed skipped: set both TEST_USER_PHONE and TEST_USER_MPIN",
            flush=True,
        )
        return None
    if not _PHONE_RE.match(phone):
        print(
            "test user seed skipped: TEST_USER_PHONE must be E.164 (e.g. +9198XXXXXXXX)",
            flush=True,
        )
        return None
    if not _MPIN_RE.match(mpin):
        print(
            "test user seed skipped: TEST_USER_MPIN must be 4–6 digits",
            flush=True,
        )
        return None

    display_name = _env("TEST_USER_NAME") or "Payments Tester"
    email = _env("TEST_USER_EMAIL").lower() or None
    now = datetime.now(timezone.utc)
    role = resolve_role_for_user(email=email, phone_number=phone)

    users.create_index("phone_number", unique=True, sparse=True)

    existing = users.find_one({"phone_number": phone})
    mpin_hash = hash_password(mpin)
    if existing is None:
        document = {
            "phone_number": phone,
            "mobile_verified": True,
            "display_name": display_name,
            "platform": "junction_website",
            "role": role,
            "account_status": "active",
            "bio": None,
            "avatar_url": None,
            "mpin_hash": mpin_hash,
            "mpin_set_at": now,
            "is_test_user": True,
            "created_at": now,
            "updated_at": now,
        }
        if email:
            document["email"] = email
        try:
            result = users.insert_one(document)
            if role != UserRole.admin.value:
                initialize_user_plan(result.inserted_id)
            user = users.find_one({"_id": result.inserted_id})
            print(f"test user seed: created {phone} ({display_name})", flush=True)
        except DuplicateKeyError:
            user = users.find_one({"phone_number": phone})
    else:
        updates: dict = {
            "mpin_hash": mpin_hash,
            "mpin_set_at": now,
            "display_name": display_name,
            "mobile_verified": True,
            "is_test_user": True,
            "updated_at": now,
        }
        if email:
            updates["email"] = email
        users.update_one({"_id": existing["_id"]}, {"$set": updates})
        user = users.find_one({"_id": existing["_id"]})
        if user is not None and user.get("plan") is None and role != UserRole.admin.value:
            initialize_user_plan(user["_id"])
            user = users.find_one({"_id": user["_id"]})
        print(f"test user seed: updated {phone} ({display_name})", flush=True)

    if user is not None:
        _ensure_test_shop(user)
    return user


def _ensure_test_shop(user: dict) -> ObjectId | None:
    """Create one shop for the test user when TEST_SHOP_NAME (+ city/locality) is set."""
    name = _env("TEST_SHOP_NAME")
    if not name:
        return None
    city = _env("TEST_SHOP_CITY") or "Bengaluru"
    locality = _env("TEST_SHOP_LOCALITY") or "Indiranagar"
    owner_id = str(user["_id"])

    existing = shops.find_one({"owner_user_id": owner_id, "name": name})
    if existing is not None:
        print(f"test shop seed: already exists {name} ({existing['_id']})", flush=True)
        return existing["_id"]

    owner = restore_persisted_plan(user)
    shop_plan, is_locked, lock_reason = plan_document_for_new_shop(owner)
    now = datetime.now(timezone.utc)
    document = {
        "name": name,
        "city": city,
        "locality": locality,
        "address": None,
        "latitude": None,
        "longitude": None,
        "open_time": None,
        "closed_time": None,
        "is_open": True,
        "show_phone": True,
        "phone_number": user.get("phone_number"),
        "currency": "INR",
        "owner_user_id": owner_id,
        "shop_type": None,
        "plan": shop_plan,
        "is_locked": is_locked,
        "lock_reason": lock_reason,
        "is_test_shop": True,
        "created_at": now,
        "updated_at": now,
    }
    try:
        shops.create_index([("owner_user_id", 1), ("name", 1)], unique=True)
        result = shops.insert_one(document)
        print(f"test shop seed: created {name} ({result.inserted_id})", flush=True)
        return result.inserted_id
    except DuplicateKeyError:
        again = shops.find_one({"owner_user_id": owner_id, "name": name})
        return again["_id"] if again else None
    except Exception as exc:
        print(f"test shop seed failed: {exc}", flush=True)
        return None
