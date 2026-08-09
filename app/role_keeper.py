import os
from datetime import datetime, timezone

from pymongo import ReturnDocument

from .database import role_keeper
from .roles import DEFAULT_USER_ROLE, UserRole

ROLE_KEEPER_DOCUMENT_ID = "singleton"
VALID_ROLES = {role.value for role in UserRole}


def _normalize_phone(phone_number: str) -> str:
    return phone_number.strip()


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _default_mappings() -> dict[str, str]:
    return {
        "+918340300635": UserRole.admin.value,
        "+919876543210": UserRole.owner.value,
        "+911111111111": UserRole.viewer.value,
        "admin@example.com": UserRole.admin.value,
    }


def get_role_keeper_document() -> dict:
    document = role_keeper.find_one({"_id": ROLE_KEEPER_DOCUMENT_ID})
    if document is None:
        now = datetime.now(timezone.utc)
        document = {
            "_id": ROLE_KEEPER_DOCUMENT_ID,
            "mappings": _default_mappings(),
            "created_at": now,
            "updated_at": now,
        }
        role_keeper.insert_one(document)
    return document


def load_role_keeper() -> dict[str, str]:
    document = get_role_keeper_document()
    mappings = document.get("mappings", {})
    if not isinstance(mappings, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in mappings.items():
        if isinstance(key, str) and isinstance(value, str):
            role = value.strip().lower()
            if role in VALID_ROLES:
                cleaned[key.strip()] = role
    return cleaned


def save_role_keeper(mappings: dict[str, str]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in mappings.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        role = value.strip().lower()
        if role not in VALID_ROLES:
            raise ValueError(f"Invalid role '{value}' for '{key}'")
        cleaned[key.strip()] = role

    now = datetime.now(timezone.utc)
    role_keeper.update_one(
        {"_id": ROLE_KEEPER_DOCUMENT_ID},
        {
            "$set": {"mappings": cleaned, "updated_at": now},
            "$setOnInsert": {"_id": ROLE_KEEPER_DOCUMENT_ID, "created_at": now},
        },
        upsert=True,
    )
    return cleaned


def resolve_role_from_keeper(email: str | None = None, phone_number: str | None = None) -> str:
    mappings = load_role_keeper()

    if phone_number:
        normalized_phone = _normalize_phone(phone_number)
        if normalized_phone in mappings:
            return mappings[normalized_phone]

    if email:
        normalized_email = _normalize_email(email)
        if normalized_email in mappings:
            return mappings[normalized_email]

    admin_email = os.getenv("ADMIN_EMAIL", "").lower().strip()
    admin_phone = os.getenv("ADMIN_PHONE", "").strip()
    if admin_email and email and _normalize_email(email) == admin_email:
        return UserRole.admin.value
    if admin_phone and phone_number and _normalize_phone(phone_number) == admin_phone:
        return UserRole.admin.value

    return DEFAULT_USER_ROLE.value
