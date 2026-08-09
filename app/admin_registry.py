import json
import os
from datetime import datetime, timezone
from pathlib import Path

ADMIN_LIST_PATH = os.getenv("ADMIN_LIST_PATH", "admin.json")

_cached_admins: dict[str, str] | None = None
_cached_mtime: float | None = None
_loaded_at: datetime | None = None


def _normalize_phone(phone_number: str) -> str:
    return phone_number.strip()


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _clean_admin_mappings(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if value.strip().lower() != "admin":
            continue
        cleaned[key.strip()] = "admin"
    return cleaned


def load_admin_registry(*, force: bool = False) -> dict[str, str]:
    """Load admin phone/email mappings from admin.json, with in-memory caching."""
    global _cached_admins, _cached_mtime, _loaded_at

    path = Path(ADMIN_LIST_PATH)
    if not path.is_absolute():
        path = Path.cwd() / path

    if not force and _cached_admins is not None and path.exists():
        mtime = path.stat().st_mtime
        if _cached_mtime == mtime:
            return _cached_admins

    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            mappings = {}
        else:
            mappings = _clean_admin_mappings(raw)
        mtime = path.stat().st_mtime
    else:
        mappings = {}
        mtime = None

    _cached_admins = mappings
    _cached_mtime = mtime
    _loaded_at = datetime.now(timezone.utc)
    return mappings


def refresh_admin_registry() -> dict[str, str]:
    """Force reload admin.json from disk."""
    return load_admin_registry(force=True)


def get_admin_registry_loaded_at() -> datetime | None:
    return _loaded_at


def is_admin_user(*, email: str | None = None, phone_number: str | None = None) -> bool:
    mappings = load_admin_registry()

    if phone_number:
        normalized_phone = _normalize_phone(phone_number)
        if normalized_phone in mappings:
            return True

    if email:
        normalized_email = _normalize_email(email)
        if normalized_email in mappings:
            return True

    admin_email = os.getenv("ADMIN_EMAIL", "").lower().strip()
    admin_phone = os.getenv("ADMIN_PHONE", "").strip()
    if admin_email and email and _normalize_email(email) == admin_email:
        return True
    if admin_phone and phone_number and _normalize_phone(phone_number) == admin_phone:
        return True

    return False
