from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator
from pymongo import ReturnDocument

from .database import users
from .login import get_current_user

router = APIRouter(prefix="/profile", tags=["profile"])


class Profile(BaseModel):
    id: str
    email: EmailStr | None = None
    phone_number: str | None = None
    display_name: str
    bio: str | None
    avatar_url: str | None
    digilocker_verified: bool = False
    digilocker_name: str | None = None
    created_at: datetime
    updated_at: datetime


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    bio: str | None = Field(default=None, max_length=500)
    avatar_url: HttpUrl | None = None

    @field_validator("display_name")
    @classmethod
    def display_name_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("display_name must not be blank")
        return value


def serialize_profile(user: dict) -> Profile:
    return Profile(
        id=str(user["_id"]),
        email=user.get("email"),
        phone_number=user.get("phone_number"),
        display_name=user["display_name"],
        bio=user.get("bio"),
        avatar_url=user.get("avatar_url"),
        digilocker_verified=bool(user.get("digilocker_verified", False)),
        digilocker_name=user.get("digilocker_name"),
        created_at=user["created_at"],
        updated_at=user["updated_at"],
    )


@router.get("", response_model=Profile)
def read_profile(current_user: Annotated[dict, Depends(get_current_user)]) -> Profile:
    return serialize_profile(current_user)


@router.patch("", response_model=Profile)
def update_profile(payload: ProfileUpdate, current_user: Annotated[dict, Depends(get_current_user)]) -> Profile:
    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        raise HTTPException(status_code=400, detail="Provide at least one profile field")
    changes["updated_at"] = datetime.now(timezone.utc)
    user = users.find_one_and_update({"_id": current_user["_id"]}, {"$set": changes}, return_document=ReturnDocument.AFTER)
    return serialize_profile(user)