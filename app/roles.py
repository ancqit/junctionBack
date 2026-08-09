from enum import Enum


class UserRole(str, Enum):
    admin = "admin"
    viewer = "viewer"
    owner = "owner"


DEFAULT_USER_ROLE = UserRole.owner


def get_user_role(user: dict) -> UserRole:
    role = user.get("role", DEFAULT_USER_ROLE.value)
    try:
        return UserRole(role)
    except ValueError:
        return DEFAULT_USER_ROLE
