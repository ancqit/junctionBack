from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from pymongo import ReturnDocument

from .database import blog_counters, blog_entries, blog_profiles

router = APIRouter(prefix="/blog", tags=["blog"])


class HourBlock(BaseModel):
    startHour: int = Field(ge=0, le=23)
    endHour: int = Field(ge=1, le=24)
    activity: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern=r"^(sleep|rest|active)$")


class DayTemplate(BaseModel):
    type: str = Field(pattern=r"^(active|rest)$")
    blocks: list[HourBlock] = Field(default_factory=list)


class BlogEntryCreate(BaseModel):
    junction: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=8000)
    creatorName: str = Field(min_length=1, max_length=100)
    creatorNumber: str = Field(min_length=1, max_length=16)
    nameTag: str = Field(min_length=1, max_length=48)
    tags: list[str] = Field(default_factory=list)

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


class BlogComment(BaseModel):
    id: str
    body: str
    creatorName: str
    creatorNumber: str
    nameTag: str
    createdAt: datetime


class BlogCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    creatorName: str = Field(min_length=1, max_length=100)
    creatorNumber: str = Field(min_length=1, max_length=16)
    nameTag: str = Field(min_length=1, max_length=48)

    @field_validator("body", "creatorName", "creatorNumber", "nameTag")
    @classmethod
    def strip_text(cls, value: str) -> str:
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
    return BlogEntry(
        id=str(document["_id"]),
        blogNumber=document["blogNumber"],
        junction=document["junction"],
        body=document["body"],
        creatorName=document["creatorName"],
        creatorNumber=document["creatorNumber"],
        nameTag=document["nameTag"],
        tags=document.get("tags") or [],
        comments=[BlogComment(**item) for item in document.get("comments") or []],
        createdAt=document["createdAt"],
        updatedAt=document["updatedAt"],
    )


@router.post("/entries", response_model=BlogEntry, status_code=status.HTTP_201_CREATED)
def create_entry(payload: BlogEntryCreate) -> BlogEntry:
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
