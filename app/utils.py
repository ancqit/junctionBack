from bson import ObjectId
from fastapi import HTTPException


def parse_object_id(value: str, resource_name: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=404, detail=f"{resource_name} not found")
    return ObjectId(value)
