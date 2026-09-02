from datetime import datetime, timezone
from uuid import uuid4
import re

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from pymongo import ReturnDocument

from .database import blog_counters, blog_entries, blog_profiles, shops
from .utils import parse_object_id

router = APIRouter(prefix="/blog", tags=["blog"])

_PHONE_DIGITS = re.compile(r"\D+")


class HourBlock(BaseModel):
    startHour: int = Field(ge=0, le=23)
    endHour: int = Field(ge=1, le=24)
    activity: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern=r"^(sleep|rest|active)$")


class DayTemplate(BaseModel):
    type: str = Field(pattern=r"^(active|rest)$")
    blocks: list[HourBlock] = Field(default_factory=list)


class BlogComment(BaseModel):
    id: str
    body: str
    creatorName: str
    creatorNumber: str
    nameTag: str
    createdAt: datetime
    authorKind: str = "person"
    shopId: str | None = None


class BlogCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    creatorName: str = Field(min_length=1, max_length=100)
    creatorNumber: str = Field(min_length=1, max_length=16)
    nameTag: str = Field(min_length=1, max_length=48)
    authorKind: str = Field(default="person", pattern=r"^(person|shop)$")
    shopId: str | None = Field(default=None, max_length=40)

    @field_validator("body", "creatorName", "creatorNumber", "nameTag")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class BlogCommentPatch(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    creatorNumber: str = Field(min_length=1, max_length=16)
    nameTag: str = Field(min_length=1, max_length=48)

    @field_validator("body", "creatorNumber", "nameTag")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class BlogCommentDelete(BaseModel):
    creatorNumber: str = Field(min_length=1, max_length=16)
    nameTag: str = Field(min_length=1, max_length=48)

    @field_validator("creatorNumber", "nameTag")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class BlogShopPhoneLookup(BaseModel):
    phone_number: str = Field(min_length=8, max_length=20)


class BlogShopIdentity(BaseModel):
    shop_id: str
    shop_name: str
    phone_number: str
    city: str | None = None
    locality: str | None = None
    creator_name: str
    creator_number: str
    name_tag: str


class BlogEntryCreate(BaseModel):
    junction: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=8000)
    creatorName: str = Field(min_length=1, max_length=100)
    creatorNumber: str = Field(min_length=1, max_length=16)
    nameTag: str = Field(min_length=1, max_length=48)
    tags: list[str] = Field(default_factory=list)
    authorKind: str = Field(default="person", pattern=r"^(person|shop)$")
    shopId: str | None = Field(default=None, max_length=40)

    @field_validator("junction", "body", "creatorName", "creatorNumber", "nameTag")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str]) -> list[str]:
        return [tag.strip() for tag in value if tag and tag.strip()][:20]


class BlogEntryPatch(BaseModel):
    body: str = Field(min_length=1, max_length=8000)

    @field_validator("body")
    @classmethod
    def strip_body(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class BlogEntry(BlogEntryCreate):
    id: str
    blogNumber: int
    comments: list[BlogComment] = Field(default_factory=list)
    createdAt: datetime
    updatedAt: datetime


class BlogProfile(BaseModel):
    userNumber: str
    displayName: str
    phoneNumber: str | None = None
    nameTag: str
    sleepTime: str
    wakeTime: str
    primaryActivity: str
    restDays: list[int] = Field(default_factory=list)
    activeDay: DayTemplate
    restDay: DayTemplate
    updatedAt: datetime | None = None


def _digits(value: str | None) -> str:
    if not value:
        return ""
    return _PHONE_DIGITS.sub("", value.strip())


def _phone_variants(phone_number: str) -> set[str]:
    digits = _digits(phone_number)
    variants = {digits}
    if digits.startswith("91") and len(digits) == 12:
        variants.add(digits[2:])
    if len(digits) == 10:
        variants.add(f"91{digits}")
    return {item for item in variants if item}


def _shop_name_tag(shop_name: str, phone_number: str) -> tuple[str, str, str]:
    digits = _digits(phone_number)
    creator_number = digits[-4:] if len(digits) >= 4 else (digits or "0000")
    slug = (
        re.sub(r"[^a-z0-9]+", "", shop_name.lower())[:16]
        or "shop"
    )
    return shop_name.strip(), creator_number, f"{slug}#{creator_number}"


def _find_shop_by_phone(phone_number: str) -> dict | None:
    variants = _phone_variants(phone_number)
    if not variants:
        return None
    for document in shops.find({"phone_number": {"$type": "string"}}).limit(500):
        stored = document.get("phone_number")
        if isinstance(stored, str) and _digits(stored) in variants:
            return document
        # Also match last-10 when stored includes country code variants above.
        stored_digits = _digits(stored if isinstance(stored, str) else "")
        if stored_digits and any(
            stored_digits.endswith(variant[-10:]) or variant.endswith(stored_digits[-10:])
            for variant in variants
            if len(variant) >= 10 and len(stored_digits) >= 10
        ):
            return document
    return None


def next_number(name: str, start: int) -> int:
    document = blog_counters.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    seq = int(document.get("seq") or 0)
    if seq < start:
        blog_counters.update_one({"_id": name}, {"$set": {"seq": start}})
        return start
    return seq


def serialize_entry(document: dict) -> BlogEntry:
    comments: list[BlogComment] = []
    for item in document.get("comments") or []:
        comments.append(
            BlogComment(
                id=item["id"],
                body=item["body"],
                creatorName=item["creatorName"],
                creatorNumber=item["creatorNumber"],
                nameTag=item["nameTag"],
                createdAt=item["createdAt"],
                authorKind=item.get("authorKind") or "person",
                shopId=item.get("shopId"),
            )
        )
    return BlogEntry(
        id=str(document["_id"]),
        blogNumber=document["blogNumber"],
        junction=document["junction"],
        body=document["body"],
        creatorName=document["creatorName"],
        creatorNumber=document["creatorNumber"],
        nameTag=document["nameTag"],
        tags=document.get("tags") or [],
        authorKind=document.get("authorKind") or "person",
        shopId=document.get("shopId"),
        comments=comments,
        createdAt=document["createdAt"],
        updatedAt=document["updatedAt"],
    )


def _owns_comment(comment: dict, creator_number: str, name_tag: str) -> bool:
    return (
        str(comment.get("creatorNumber", "")).strip() == creator_number.strip()
        and str(comment.get("nameTag", "")).strip().lower() == name_tag.strip().lower()
    )


@router.post("/verify-shop-phone", response_model=BlogShopIdentity)
def verify_shop_phone(payload: BlogShopPhoneLookup) -> BlogShopIdentity:
    """Validate that a phone number belongs to a registered shop."""
    document = _find_shop_by_phone(payload.phone_number)
    if document is None:
        raise HTTPException(
            status_code=404,
            detail="No shop found for this phone number",
        )
    phone = document.get("phone_number") or payload.phone_number
    creator_name, creator_number, name_tag = _shop_name_tag(document["name"], str(phone))
    return BlogShopIdentity(
        shop_id=str(document["_id"]),
        shop_name=document["name"],
        phone_number=str(phone),
        city=document.get("city"),
        locality=document.get("locality"),
        creator_name=creator_name,
        creator_number=creator_number,
        name_tag=name_tag,
    )


@router.post("/entries", response_model=BlogEntry, status_code=status.HTTP_201_CREATED)
def create_entry(payload: BlogEntryCreate) -> BlogEntry:
    if payload.authorKind == "shop":
        if not payload.shopId:
            raise HTTPException(status_code=400, detail="shopId is required when authoring as a shop")
        oid = parse_object_id(payload.shopId, "Shop")
        shop = shops.find_one({"_id": oid})
        if shop is None:
            raise HTTPException(status_code=404, detail="Shop not found")
    now = datetime.now(timezone.utc)
    document = {
        **payload.model_dump(),
        "comments": [],
        "blogNumber": next_number("blog", 2001),
        "createdAt": now,
        "updatedAt": now,
    }
    result = blog_entries.insert_one(document)
    document["_id"] = result.inserted_id
    return serialize_entry(document)


@router.get("/entries", response_model=list[BlogEntry])
def list_entries(
    q: str | None = Query(default=None),
    tag: str | None = Query(default=None),
) -> list[BlogEntry]:
    query: dict = {}
    if q and q.strip().isdigit():
        query["blogNumber"] = int(q.strip())
    elif q and q.strip():
        needle = q.strip()
        query["$or"] = [
            {"junction": {"$regex": needle, "$options": "i"}},
            {"body": {"$regex": needle, "$options": "i"}},
            {"nameTag": {"$regex": needle, "$options": "i"}},
            {"tags": {"$regex": needle, "$options": "i"}},
        ]
    if tag and tag.strip():
        query["nameTag"] = {"$regex": tag.strip(), "$options": "i"}
    return [
        serialize_entry(doc)
        for doc in blog_entries.find(query).sort("blogNumber", -1).limit(200)
    ]


@router.get("/entries/{blog_number}", response_model=BlogEntry)
def read_entry(blog_number: int) -> BlogEntry:
    document = blog_entries.find_one({"blogNumber": blog_number})
    if document is None:
        raise HTTPException(status_code=404, detail="Blog entry not found")
    return serialize_entry(document)


@router.patch("/entries/{blog_number}", response_model=BlogEntry)
def update_entry(blog_number: int, payload: BlogEntryPatch) -> BlogEntry:
    document = blog_entries.find_one_and_update(
        {"blogNumber": blog_number},
        {"$set": {"body": payload.body, "updatedAt": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Blog entry not found")
    return serialize_entry(document)


@router.post(
    "/entries/{blog_number}/comments",
    response_model=BlogEntry,
    status_code=status.HTTP_201_CREATED,
)
def add_comment(blog_number: int, payload: BlogCommentCreate) -> BlogEntry:
    if payload.authorKind == "shop":
        if not payload.shopId:
            raise HTTPException(status_code=400, detail="shopId is required when authoring as a shop")
        oid = parse_object_id(payload.shopId, "Shop")
        if shops.find_one({"_id": oid}) is None:
            raise HTTPException(status_code=404, detail="Shop not found")
    now = datetime.now(timezone.utc)
    comment = {
        "id": str(uuid4()),
        **payload.model_dump(),
        "createdAt": now,
    }
    document = blog_entries.find_one_and_update(
        {"blogNumber": blog_number},
        {"$push": {"comments": comment}, "$set": {"updatedAt": now}},
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Blog entry not found")
    return serialize_entry(document)


@router.patch(
    "/entries/{blog_number}/comments/{comment_id}",
    response_model=BlogEntry,
)
def patch_comment(blog_number: int, comment_id: str, payload: BlogCommentPatch) -> BlogEntry:
    document = blog_entries.find_one({"blogNumber": blog_number})
    if document is None:
        raise HTTPException(status_code=404, detail="Blog entry not found")
    comments = list(document.get("comments") or [])
    found = False
    for comment in comments:
        if comment.get("id") != comment_id:
            continue
        if not _owns_comment(comment, payload.creatorNumber, payload.nameTag):
            raise HTTPException(status_code=403, detail="Not allowed to edit this comment")
        comment["body"] = payload.body
        found = True
        break
    if not found:
        raise HTTPException(status_code=404, detail="Comment not found")
    now = datetime.now(timezone.utc)
    updated = blog_entries.find_one_and_update(
        {"blogNumber": blog_number},
        {"$set": {"comments": comments, "updatedAt": now}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Blog entry not found")
    return serialize_entry(updated)


@router.delete(
    "/entries/{blog_number}/comments/{comment_id}",
    response_model=BlogEntry,
)
def delete_comment(blog_number: int, comment_id: str, payload: BlogCommentDelete) -> BlogEntry:
    document = blog_entries.find_one({"blogNumber": blog_number})
    if document is None:
        raise HTTPException(status_code=404, detail="Blog entry not found")
    comments = list(document.get("comments") or [])
    kept: list[dict] = []
    found = False
    for comment in comments:
        if comment.get("id") != comment_id:
            kept.append(comment)
            continue
        if not _owns_comment(comment, payload.creatorNumber, payload.nameTag):
            raise HTTPException(status_code=403, detail="Not allowed to delete this comment")
        found = True
    if not found:
        raise HTTPException(status_code=404, detail="Comment not found")
    now = datetime.now(timezone.utc)
    updated = blog_entries.find_one_and_update(
        {"blogNumber": blog_number},
        {"$set": {"comments": kept, "updatedAt": now}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Blog entry not found")
    return serialize_entry(updated)


@router.get("/profiles", response_model=list[BlogProfile])
def list_profiles() -> list[BlogProfile]:
    return [
        BlogProfile(**{key: value for key, value in document.items() if key != "_id"})
        for document in blog_profiles.find().limit(200)
    ]


@router.get("/profiles/{user_number}", response_model=BlogProfile)
def read_profile(user_number: str) -> BlogProfile:
    document = blog_profiles.find_one({"userNumber": user_number})
    if document is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return BlogProfile(**{key: value for key, value in document.items() if key != "_id"})


@router.put("/profiles/{user_number}", response_model=BlogProfile)
def upsert_profile(user_number: str, payload: BlogProfile) -> BlogProfile:
    now = datetime.now(timezone.utc)
    data = payload.model_dump()
    data["userNumber"] = user_number
    data["updatedAt"] = now
    blog_profiles.update_one({"userNumber": user_number}, {"$set": data}, upsert=True)
    return BlogProfile(**data)
