import os

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, HttpUrl, field_validator

from .gemini import generate_text, parse_json_string_array

router = APIRouter(prefix="/queries", tags=["queries"])

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
SUGGESTED_IMAGE_COUNT = 3

IMAGE_SEARCH_PROMPT = (
    "You help online shop owners find stock photos for product cards. "
    "Given a product name, return a JSON array of exactly {count} short English search phrases "
    "(2-6 words each) suitable for a stock photo website. "
    "Use different angles such as product on white background, lifestyle use, and close-up detail. "
    "Return only valid JSON: an array of strings.\n\n"
    "Product name: {product_name}"
)


class QuerySearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    page: int = Field(default=1, ge=1, le=100)
    per_page: int = Field(default=20, ge=1, le=80)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value


class ImageResult(BaseModel):
    id: str
    cdn_url: HttpUrl
    thumbnail_url: HttpUrl
    alt: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    source: str = "pexels"
    photographer: str | None = None
    photographer_url: HttpUrl | None = None


class QuerySearchResponse(BaseModel):
    query: str
    page: int
    per_page: int
    total_results: int
    images: list[ImageResult]


class ProductImageSuggestRequest(BaseModel):
    product_name: str = Field(min_length=1, max_length=200)

    @field_validator("product_name")
    @classmethod
    def product_name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("product_name must not be blank")
        return value


class ProductImageSuggestResponse(BaseModel):
    product_name: str
    search_queries: list[str]
    images: list[ImageResult]


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


def search_cdn_images(query: str, page: int, per_page: int) -> QuerySearchResponse:
    api_key = require_pexels_configuration()
    try:
        response = httpx.get(
            PEXELS_SEARCH_URL,
            headers={"Authorization": api_key},
            params={"query": query, "page": page, "per_page": per_page},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to reach image CDN provider",
        ) from exc

    if response.status_code == 401:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Invalid PEXELS_API_KEY")
    if response.is_error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Image CDN search failed")

    payload = response.json()
    photos = payload.get("photos", [])
    return QuerySearchResponse(
        query=query,
        page=page,
        per_page=per_page,
        total_results=payload.get("total_results", len(photos)),
        images=[map_pexels_photo(photo) for photo in photos],
    )


def suggest_image_search_queries(product_name: str, count: int = SUGGESTED_IMAGE_COUNT) -> list[str]:
    prompt = IMAGE_SEARCH_PROMPT.format(count=count, product_name=product_name)
    response_text = generate_text(prompt)
    queries = parse_json_string_array(response_text)
    return queries[:count]


def collect_suggested_images(product_name: str) -> ProductImageSuggestResponse:
    search_queries = suggest_image_search_queries(product_name)
    images: list[ImageResult] = []
    seen_ids: set[str] = set()

    for query in search_queries:
        result = search_cdn_images(query, page=1, per_page=5)
        for image in result.images:
            if image.id in seen_ids:
                continue
            images.append(image)
            seen_ids.add(image.id)
            if len(images) >= SUGGESTED_IMAGE_COUNT:
                break
        if len(images) >= SUGGESTED_IMAGE_COUNT:
            break

    if len(images) < SUGGESTED_IMAGE_COUNT:
        fallback = search_cdn_images(product_name, page=1, per_page=SUGGESTED_IMAGE_COUNT * 2)
        for image in fallback.images:
            if image.id in seen_ids:
                continue
            images.append(image)
            seen_ids.add(image.id)
            if len(images) >= SUGGESTED_IMAGE_COUNT:
                break

    if not images:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No images found for this product name",
        )

    return ProductImageSuggestResponse(
        product_name=product_name,
        search_queries=search_queries,
        images=images[:SUGGESTED_IMAGE_COUNT],
    )


@router.get("", response_model=QuerySearchResponse)
def search_images(
    query: str = Query(min_length=1, max_length=200),
    page: int = Query(default=1, ge=1, le=100),
    per_page: int = Query(default=20, ge=1, le=80),
) -> QuerySearchResponse:
    cleaned_query = query.strip()
    if not cleaned_query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="query must not be blank")
    return search_cdn_images(cleaned_query, page, per_page)


@router.post("", response_model=QuerySearchResponse, status_code=status.HTTP_200_OK)
def search_images_post(payload: QuerySearchRequest) -> QuerySearchResponse:
    return search_cdn_images(payload.query, payload.page, payload.per_page)


@router.post("/suggest-images", response_model=ProductImageSuggestResponse, status_code=status.HTTP_200_OK)
def suggest_product_images(payload: ProductImageSuggestRequest) -> ProductImageSuggestResponse:
    return collect_suggested_images(payload.product_name)
