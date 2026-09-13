"""jMonster locality / city / global content posts."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from .database import monster_posts
from .rate_limit import RATE_LIMIT_AUTH, RATE_LIMIT_CATALOG, limiter
from .session import JunctionSession
from .utils import parse_object_id

router = APIRouter(prefix="/monster", tags=["monster"])

MonsterScope = Literal["locality", "city", "global"]


class MonsterPostCreate(BaseModel):
    author_name: str = Field(min_length=2, max_length=80)
    title: str = Field(default="", max_length=120)
    body: str = Field(min_length=1, max_length=4000)
    scope: MonsterScope = "locality"
    city: str | None = Field(default=None, max_length=80)
    locality: str | None = Field(default=None, max_length=120)

    @field_validator("author_name", "title", "body", "city", "locality", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class MonsterPost(BaseModel):
    id: str
    author_name: str
    title: str
    body: str
    scope: MonsterScope
    city: str | None = None
    locality: str | None = None
    created_at: datetime


class MonsterPostList(BaseModel):
    scope: MonsterScope
    city: str | None = None
    locality: str | None = None
    total: int
    posts: list[MonsterPost]


def _ensure_indexes() -> None:
    monster_posts.create_index([("scope", 1), ("created_at", -1)])
    monster_posts.create_index([("city", 1), ("locality", 1), ("created_at", -1)])
    monster_posts.create_index([("city", 1), ("created_at", -1)])


def _serialize(document: dict) -> MonsterPost:
    return MonsterPost(
        id=str(document["_id"]),
        author_name=str(document.get("author_name") or ""),
        title=str(document.get("title") or ""),
        body=str(document.get("body") or ""),
        scope=document.get("scope") or "locality",
        city=(str(document["city"]).strip() or None) if document.get("city") else None,
        locality=(str(document["locality"]).strip() or None) if document.get("locality") else None,
        created_at=document.get("created_at") or datetime.now(timezone.utc),
    )


def _require_place(scope: MonsterScope, city: str | None, locality: str | None) -> tuple[str | None, str | None]:
    city_name = (city or "").strip()
    locality_name = (locality or "").strip()
    if scope == "global":
        return None, None
    if scope == "city":
        if not city_name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="city is required for city scope")
        return city_name, None
    if not city_name or not locality_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="city and locality are required for locality scope",
        )
    return city_name, locality_name


@router.post("/posts", response_model=MonsterPost, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_AUTH)
def create_monster_post(
    request: Request,
    payload: MonsterPostCreate,
    _: JunctionSession,
) -> MonsterPost:
    """Create a jMonster post at locality, city, or global scope."""
    _ensure_indexes()
    city, locality = _require_place(payload.scope, payload.city, payload.locality)
    now = datetime.now(timezone.utc)
    document = {
        "author_name": payload.author_name,
        "title": payload.title,
        "body": payload.body,
        "scope": payload.scope,
        "city": city,
        "locality": locality,
        "created_at": now,
    }
    result = monster_posts.insert_one(document)
    document["_id"] = result.inserted_id
    return _serialize(document)


@router.get("/posts", response_model=MonsterPostList)
@limiter.limit(RATE_LIMIT_CATALOG)
def list_monster_posts(
    request: Request,
    _: JunctionSession,
    scope: MonsterScope = Query(default="locality"),
    city: str | None = Query(default=None, max_length=80),
    locality: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=40, ge=1, le=80),
    offset: int = Query(default=0, ge=0),
) -> MonsterPostList:
    """List jMonster posts for one layer (locality / city / global)."""
    _ensure_indexes()
    city_name, locality_name = _require_place(scope, city, locality)
    query: dict = {"scope": scope}
    if scope == "locality":
        query["city"] = {"$regex": f"^{re.escape(city_name or '')}$", "$options": "i"}
        query["locality"] = {"$regex": f"^{re.escape(locality_name or '')}$", "$options": "i"}
    elif scope == "city":
        query["city"] = {"$regex": f"^{re.escape(city_name or '')}$", "$options": "i"}

    total = monster_posts.count_documents(query)
    documents = (
        monster_posts.find(query).sort("created_at", -1).skip(offset).limit(limit)
    )
    return MonsterPostList(
        scope=scope,
        city=city_name,
        locality=locality_name,
        total=total,
        posts=[_serialize(doc) for doc in documents],
    )


@router.get("/posts/{post_id}", response_model=MonsterPost)
@limiter.limit(RATE_LIMIT_CATALOG)
def get_monster_post(request: Request, post_id: str, _: JunctionSession) -> MonsterPost:
    document = monster_posts.find_one({"_id": parse_object_id(post_id, "Post")})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    return _serialize(document)
