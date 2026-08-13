from typing import Annotated

from bson import ObjectId
from fastapi import Depends, HTTPException, Query


def parse_object_id(value: str, resource_name: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=404, detail=f"{resource_name} not found")
    return ObjectId(value)


_TRUE_VALUES = {"1", "true", "yes", "on", "y"}
_FALSE_VALUES = {"0", "false", "no", "off", "n", ""}


def parse_bool_query(value: str | bool | None, field_name: str = "show_phone") -> bool:
    """Accept true/false/1/0/yes/no, including empty (?show_phone) as false."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    raise HTTPException(
        status_code=400,
        detail=f"{field_name} must be true or false",
    )


def show_phone_query(
    show_phone: Annotated[
        str | None,
        Query(description="true to reveal shop mobile numbers"),
    ] = None,
) -> bool:
    return parse_bool_query(show_phone, "show_phone")


ShowPhone = Annotated[bool, Depends(show_phone_query)]
