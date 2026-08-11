"""Pexels CDN image search."""

import os

import httpx
from fastapi import HTTPException, status

from .image_models import ImageResult

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
MAX_PEXELS_PER_PAGE = 80
DEFAULT_SUGGESTED_IMAGE_COUNT = 10


def require_pexels_configuration() -> str:
    api_key = PEXELS_API_KEY.strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PEXELS_API_KEY is not configured",
        )
    return api_key


def map_pexels_photo(photo: dict) -> ImageResult:
    source = photo.get("src", {})
    return ImageResult(
        id=str(photo["id"]),
        cdn_url=source.get("large2x") or source.get("large") or source.get("original"),
        thumbnail_url=source.get("medium") or source.get("small") or source.get("large"),
        alt=photo.get("alt") or "Image",
        width=photo.get("width", 1),
        height=photo.get("height", 1),
        source="pexels",
        photographer=photo.get("photographer"),
        photographer_url=photo.get("photographer_url"),
    )


def search_pexels_images(query: str, page: int, per_page: int) -> tuple[int, list[ImageResult]]:
    """Return (total_results, images) from Pexels search."""
    api_key = require_pexels_configuration()
    cleaned = query.strip()
    if not cleaned:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="query must not be blank")

    page = max(page, 1)
    per_page = min(max(per_page, 1), MAX_PEXELS_PER_PAGE)

    try:
        response = httpx.get(
            PEXELS_SEARCH_URL,
            headers={"Authorization": api_key},
            params={"query": cleaned, "page": page, "per_page": per_page},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to reach Pexels",
        ) from exc

    if response.status_code == 401:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Invalid PEXELS_API_KEY",
        )
    if response.is_error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Pexels image search failed",
        )

    payload = response.json()
    photos = payload.get("photos") or []
    images = [map_pexels_photo(photo) for photo in photos]
    total = int(payload.get("total_results", len(images)))
    return total, images
