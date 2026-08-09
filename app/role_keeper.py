import json
import os
from pathlib import Path

from .roles import DEFAULT_USER_ROLE, UserRole

ROLE_KEEPER_PATH = os.getenv("ROLE_KEEPER_PATH", "role_keeper.json")
VALID_ROLES = {role.value for role in UserRole}


def _normalize_phone(phone_number: str) -> str:
    return phone_number.strip()


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _keeper_paths() -> list[Path]:
    paths = [
        Path(ROLE_KEEPER_PATH),
        Path("role_keeper.json"),
        Path("/etc/secrets/role_keeper.json"),
    ]
    unique_paths: list[Path] = []
    for path in paths:
        if path not in unique_paths:
            unique_paths.append(path)
    return unique_paths


def load_role_keeper() -> dict[str, str]:
    """Load phone/email -> role mappings from the keeper file."""
    mappings: dict[str, str] = {}

    for path in _keeper_paths():
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in {"admins", "owners", "viewers"} and isinstance(value, list):
                    role_name = key[:-1] if key.endswith("s") else key
                    if role_name not in VALID_ROLES:
                        continue
                    for entry in value:
                        if isinstance(entry, str) and entry.strip():
                            mappings[entry.strip()] = role_name
                    continue

                if isinstance(key, str) and isinstance(value, str):
                    role = value.strip().lower()
                    if role in VALID_ROLES:
                        mappings[key.strip()] = role

        if mappings:
            return mappings

    return mappings


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
