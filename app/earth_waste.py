"""Public jEarth waste archive search API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from .waste_crawler import search_waste_archive, seed_archive, ensure_waste_archive_indexes

router = APIRouter(prefix="/earth/waste", tags=["earth-waste"])


class WasteSource(BaseModel):
    url: str = ""
    title: str = ""


class WasteSearchHit(BaseModel):
    id: str
    title: str
    stream: str
    snippet: str = ""
    dispose: list[str] = Field(default_factory=list)
    sources: list[WasteSource] = Field(default_factory=list)
    score: float | None = None


class WasteSearchResponse(BaseModel):
    query: str
    lang: str
    results: list[WasteSearchHit]


@router.get("/search", response_model=WasteSearchResponse)
def earth_waste_search(
    q: str = Query(min_length=1, max_length=120),
    lang: str = Query(default="en", pattern="^(en|hi)$"),
    limit: int = Query(default=12, ge=1, le=30),
) -> WasteSearchResponse:
    """Google-like waste search — archive loaded from Mongo, refreshed by the crawler helper."""
    from .database import waste_archive

    try:
        ensure_waste_archive_indexes()
    except Exception:
        pass
    try:
        if waste_archive.count_documents({}, limit=1) == 0:
            seed_archive(force=True)
    except Exception:
        pass
    hits = search_waste_archive(q, lang=lang, limit=limit)
    return WasteSearchResponse(
        query=q.strip(),
        lang=lang,
        results=[WasteSearchHit(**hit) for hit in hits],
    )


@router.get("/health")
def earth_waste_health() -> dict[str, Any]:
    from .database import waste_archive

    try:
        count = waste_archive.count_documents({})
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "archive_count": count}
