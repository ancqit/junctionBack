"""jEarth Home trash: a customer's bucket of waste items, unlocked with the Junction MPIN.

Items are snapshots of `waste_archive` entries taken when added, so the PDF
stays stable even if the archive entry is later edited or re-learned.

Avoid `from __future__ import annotations`: with slowapi it makes body models look like query params (422).
"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

from .catalog_contacts import ContactAuth, contact_display_name
from .database import home_trash, waste_archive
from .home_trash_pdf import (
    HomeTrashInput,
    TrashItem,
    home_trash_filename,
    render_home_trash,
)
from .rate_limit import RATE_LIMIT_CATALOG, limiter

router = APIRouter(prefix="/earth/home-trash", tags=["earth-home-trash"])

MAX_ITEMS = 100
SNAPSHOT_FIELDS = (
    "name_en",
    "name_hi",
    "stream",
    "dispose_en",
    "dispose_hi",
    "snippet_en",
    "snippet_hi",
    "sources",
    "origin",
)

_indexes_ready = False


class HomeTrashAdd(BaseModel):
    entry_id: str = Field(min_length=1, max_length=120)


class HomeTrashSource(BaseModel):
    url: str = ""
    title: str = ""


class HomeTrashItem(BaseModel):
    entry_id: str
    title_en: str
    title_hi: str
    stream: str
    dispose_en: list[str] = Field(default_factory=list)
    dispose_hi: list[str] = Field(default_factory=list)
    snippet_en: str = ""
    snippet_hi: str = ""
    sources: list[HomeTrashSource] = Field(default_factory=list)
    origin: str | None = None
    added_at: datetime


class HomeTrashList(BaseModel):
    items: list[HomeTrashItem]
    count: int
    max_items: int = MAX_ITEMS


def _ensure_indexes() -> None:
    global _indexes_ready
    if _indexes_ready:
        return
    home_trash.create_index([("contact_id", 1), ("entry_id", 1)], unique=True)
    home_trash.create_index([("contact_id", 1), ("added_at", -1)])
    _indexes_ready = True


def _serialize(doc: dict) -> HomeTrashItem:
    return HomeTrashItem(
        entry_id=doc["entry_id"],
        title_en=doc.get("name_en") or doc.get("name_hi") or doc["entry_id"],
        title_hi=doc.get("name_hi") or doc.get("name_en") or doc["entry_id"],
        stream=doc.get("stream") or "reject",
        dispose_en=list(doc.get("dispose_en") or []),
        dispose_hi=list(doc.get("dispose_hi") or []),
        snippet_en=doc.get("snippet_en") or "",
        snippet_hi=doc.get("snippet_hi") or "",
        sources=[
            HomeTrashSource(**s)
            for s in doc.get("sources") or []
            if isinstance(s, dict)
        ],
        origin=doc.get("origin"),
        added_at=doc["added_at"],
    )


def _items_for(contact: dict) -> list[dict]:
    _ensure_indexes()
    return list(
        home_trash.find({"contact_id": contact["_id"]})
        .sort("added_at", -1)
        .limit(MAX_ITEMS)
    )


@router.get("", response_model=HomeTrashList)
@limiter.limit(RATE_LIMIT_CATALOG)
def list_home_trash(request: Request, contact: ContactAuth) -> HomeTrashList:
    items = [_serialize(doc) for doc in _items_for(contact)]
    return HomeTrashList(items=items, count=len(items))


@router.post("", response_model=HomeTrashItem)
@limiter.limit(RATE_LIMIT_CATALOG)
def add_to_home_trash(
    request: Request, payload: HomeTrashAdd, contact: ContactAuth
) -> HomeTrashItem:
    _ensure_indexes()
    entry_id = payload.entry_id.strip()
    existing = home_trash.find_one({"contact_id": contact["_id"], "entry_id": entry_id})
    if existing:
        return _serialize(existing)
    entry = waste_archive.find_one({"id": entry_id}, {"_id": 0})
    if entry is None:
        raise HTTPException(
            status_code=404, detail="This item is not in the waste archive"
        )
    if home_trash.count_documents({"contact_id": contact["_id"]}) >= MAX_ITEMS:
        raise HTTPException(
            status_code=409, detail=f"Home trash holds up to {MAX_ITEMS} items"
        )
    doc = home_trash.find_one_and_update(
        {"contact_id": contact["_id"], "entry_id": entry_id},
        {
            "$setOnInsert": {
                "contact_id": contact["_id"],
                "entry_id": entry_id,
                "added_at": datetime.now(timezone.utc),
                **{key: entry.get(key) for key in SNAPSHOT_FIELDS},
            }
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(doc)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(RATE_LIMIT_CATALOG)
def remove_from_home_trash(
    request: Request, entry_id: str, contact: ContactAuth
) -> Response:
    _ensure_indexes()
    home_trash.delete_one({"contact_id": contact["_id"], "entry_id": entry_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(RATE_LIMIT_CATALOG)
def empty_home_trash(request: Request, contact: ContactAuth) -> Response:
    _ensure_indexes()
    home_trash.delete_many({"contact_id": contact["_id"]})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/pdf")
@limiter.limit(RATE_LIMIT_CATALOG)
def home_trash_pdf(
    request: Request,
    contact: ContactAuth,
    lang: Literal["en", "hi"] = Query(default="en"),
) -> Response:
    items = [_serialize(doc) for doc in _items_for(contact)]
    now = datetime.now(timezone.utc)
    data = HomeTrashInput(
        display_name=contact_display_name(contact),
        phone_number=contact["phone_number"],
        created_at=now,
        items=[
            TrashItem(
                title_en=item.title_en,
                title_hi=item.title_hi,
                stream=item.stream,
                dispose_en=item.dispose_en,
                dispose_hi=item.dispose_hi,
                source_url=item.sources[0].url if item.sources else "",
            )
            for item in reversed(items)
        ],
    )
    return Response(
        content=render_home_trash(data, lang),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{home_trash_filename(now)}"',
            "Cache-Control": "private, no-store",
        },
    )
