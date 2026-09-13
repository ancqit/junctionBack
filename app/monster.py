"""jMonster shorts — locality / city / global, person or shop authors.

Storage is intentionally lean: tiny text caps, omit empty fields, and a TTL so
old shorts expire instead of growing the collection forever.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from .access_control import require_store_access
from .database import monster_posts, shops
from .rate_limit import RATE_LIMIT_AUTH, RATE_LIMIT_CATALOG, limiter
from .session import CatalogReader, is_junction_session
from .utils import parse_object_id

router = APIRouter(prefix="/monster", tags=["monster"])

MonsterScope = Literal["locality", "city", "global"]
MonsterAuthorKind = Literal["person", "shop"]

# True “short” copy — tweet-length, not blogs.
SHORT_BODY_MAX = 280
SHORT_TITLE_MAX = 48
SHORT_AUTHOR_MAX = 60
# Auto-delete after 90 days so Promotion / feeds stay fresh and small.
SHORT_TTL_DAYS = 90
SHORT_TTL_SECONDS = SHORT_TTL_DAYS * 24 * 60 * 60

_WHITESPACE_RE = re.compile(r"\s+")


def _compact_text(value: str) -> str:
    """Strip and collapse runs of whitespace to one space (smaller BSON + cleaner UI)."""
    return _WHITESPACE_RE.sub(" ", value).strip()


class MonsterPostCreate(BaseModel):
    author_name: str = Field(min_length=2, max_length=SHORT_AUTHOR_MAX)
    title: str = Field(default="", max_length=SHORT_TITLE_MAX)
    body: str = Field(min_length=1, max_length=SHORT_BODY_MAX)
    scope: MonsterScope = "locality"
    city: str | None = Field(default=None, max_length=80)
    locality: str | None = Field(default=None, max_length=120)
    author_kind: MonsterAuthorKind = "person"
    shop_id: str | None = Field(default=None, max_length=80)

    @field_validator("author_name", "title", "body", "city", "locality", "shop_id", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return _compact_text(value)
        return value


class MonsterPost(BaseModel):
    id: str
    author_name: str
    title: str = ""
    body: str
    scope: MonsterScope
    city: str | None = None
    locality: str | None = None
    author_kind: MonsterAuthorKind = "person"
    shop_id: str | None = None
    shop_name: str | None = None
    created_at: datetime


class MonsterPostList(BaseModel):
    scope: MonsterScope | None = None
    city: str | None = None
    locality: str | None = None
    total: int
    posts: list[MonsterPost]


def _ensure_indexes() -> None:
    monster_posts.create_index([("scope", 1), ("created_at", -1)])
    monster_posts.create_index([("city", 1), ("locality", 1), ("created_at", -1)])
    monster_posts.create_index([("city", 1), ("created_at", -1)])
    monster_posts.create_index([("shop_id", 1), ("created_at", -1)])
    monster_posts.create_index([("author_kind", 1), ("created_at", -1)])
    # TTL index — MongoDB drops documents SHORT_TTL_SECONDS after created_at.
    monster_posts.create_index("created_at", expireAfterSeconds=SHORT_TTL_SECONDS)


def _serialize(document: dict) -> MonsterPost:
    author_kind = document.get("author_kind") or "person"
    shop_name = (str(document["shop_name"]).strip() or None) if document.get("shop_name") else None
    author_name = str(document.get("author_name") or "").strip()
    if not author_name and shop_name:
        author_name = shop_name
    return MonsterPost(
        id=str(document["_id"]),
        author_name=author_name,
        title=str(document.get("title") or ""),
        body=str(document.get("body") or ""),
        scope=document.get("scope") or "locality",
        city=(str(document["city"]).strip() or None) if document.get("city") else None,
        locality=(str(document["locality"]).strip() or None) if document.get("locality") else None,
        author_kind=author_kind,
        shop_id=(str(document["shop_id"]).strip() or None) if document.get("shop_id") else None,
        shop_name=shop_name,
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


def _resolve_shop(shop_id: str) -> dict:
    document = shops.find_one({"_id": parse_object_id(shop_id, "Shop")})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    return document


def _create_short_document(payload: MonsterPostCreate, *, shop_doc: dict | None) -> dict:
    city, locality = _require_place(payload.scope, payload.city, payload.locality)
    # Shop shorts inherit shop city/locality when scope is locality/city and place omitted.
    if shop_doc is not None:
        shop_city = str(shop_doc.get("city") or "").strip()
        shop_locality = str(shop_doc.get("locality") or "").strip()
        if payload.scope == "locality":
            city = city or shop_city
            locality = locality or shop_locality
            city, locality = _require_place("locality", city, locality)
        elif payload.scope == "city":
            city = city or shop_city
            city, locality = _require_place("city", city, None)

    now = datetime.now(timezone.utc)
    # Lean BSON: skip empty optional keys; shop shorts store shop_name only (no duplicated author_name).
    document: dict = {
        "body": payload.body,
        "scope": payload.scope,
        "author_kind": payload.author_kind,
        "created_at": now,
    }
    if payload.title:
        document["title"] = payload.title
    if city:
        document["city"] = city
    if locality:
        document["locality"] = locality

    if payload.author_kind == "shop":
        if shop_doc is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="shop_id is required for shop shorts")
        document["shop_id"] = str(shop_doc["_id"])
        shop_name = str(shop_doc.get("name") or "").strip()
        if shop_name:
            document["shop_name"] = shop_name
    else:
        document["author_name"] = payload.author_name

    return document


@router.post("/posts", response_model=MonsterPost, status_code=status.HTTP_201_CREATED)
@router.post("/shorts", response_model=MonsterPost, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_AUTH)
def create_monster_post(
    request: Request,
    payload: MonsterPostCreate,
    auth: CatalogReader,
) -> MonsterPost:
    """
    Create a short for jMonster / junction.today Promotion.

    - Guest session: person shorts, or shop shorts with a real `shop_id`.
    - Owner JWT: shop shorts must be for a shop the owner can access.
    - Body max SHORT_BODY_MAX; documents expire after SHORT_TTL_DAYS.
    """
    _ensure_indexes()
    shop_doc = None
    if payload.author_kind == "shop":
        shop_id = (payload.shop_id or "").strip()
        if not shop_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="shop_id is required for shop shorts")
        shop_doc = _resolve_shop(shop_id)
        if not is_junction_session(auth):
            require_store_access(auth["user"], shop_id)

    document = _create_short_document(payload, shop_doc=shop_doc)
    result = monster_posts.insert_one(document)
    document["_id"] = result.inserted_id
    return _serialize(document)


@router.get("/posts", response_model=MonsterPostList)
@router.get("/shorts", response_model=MonsterPostList)
@limiter.limit(RATE_LIMIT_CATALOG)
def list_monster_posts(
    request: Request,
    _: CatalogReader,
    scope: MonsterScope | None = Query(default=None),
    city: str | None = Query(default=None, max_length=80),
    locality: str | None = Query(default=None, max_length=120),
    shop_id: str | None = Query(default=None, max_length=80),
    author_kind: MonsterAuthorKind | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> MonsterPostList:
    """List shorts for a layer and/or one shop (Promotion on junction.today)."""
    _ensure_indexes()
    query: dict = {}
    city_name: str | None = None
    locality_name: str | None = None
    resolved_scope: MonsterScope | None = scope

    if shop_id and shop_id.strip():
        query["shop_id"] = shop_id.strip()
    if author_kind:
        query["author_kind"] = author_kind

    if scope:
        city_name, locality_name = _require_place(scope, city, locality)
        query["scope"] = scope
        if scope == "locality":
            query["city"] = {"$regex": f"^{re.escape(city_name or '')}$", "$options": "i"}
            query["locality"] = {"$regex": f"^{re.escape(locality_name or '')}$", "$options": "i"}
        elif scope == "city":
            query["city"] = {"$regex": f"^{re.escape(city_name or '')}$", "$options": "i"}
    elif not shop_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide scope (+ city/locality) or shop_id",
        )

    total = monster_posts.count_documents(query)
    documents = monster_posts.find(query).sort("created_at", -1).skip(offset).limit(limit)
    return MonsterPostList(
        scope=resolved_scope,
        city=city_name,
        locality=locality_name,
        total=total,
        posts=[_serialize(doc) for doc in documents],
    )


@router.get("/posts/{post_id}", response_model=MonsterPost)
@router.get("/shorts/{post_id}", response_model=MonsterPost)
@limiter.limit(RATE_LIMIT_CATALOG)
def get_monster_post(request: Request, post_id: str, _: CatalogReader) -> MonsterPost:
    document = monster_posts.find_one({"_id": parse_object_id(post_id, "Post")})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Short not found")
    return _serialize(document)
