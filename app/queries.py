from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, field_validator

from .access_control import AuthenticatedUser
from .gemini_images import (
    MAX_GENERATED_IMAGES_PER_REQUEST,
    PRODUCT_IMAGE_STYLES,
    generate_product_images,
)
from .image_models import ImageResult
from .plan_service import require_active_plan
from .rate_limit import RATE_LIMIT_AI, limiter

router = APIRouter(prefix="/queries", tags=["queries"])

DEFAULT_SUGGESTED_IMAGE_COUNT = 10


class QuerySearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    page: int = Field(default=1, ge=1, le=100)
    per_page: int = Field(default=10, ge=1, le=MAX_GENERATED_IMAGES_PER_REQUEST)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value


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
    styles: list[str]
    images: list[ImageResult]


def request_base_url(request: Request) -> str:
    configured = request.headers.get("x-forwarded-proto")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if configured and host:
        return f"{configured}://{host}"
    return str(request.base_url).rstrip("/")


def build_query_search_response(
    *,
    query: str,
    page: int,
    per_page: int,
    base_url: str,
) -> QuerySearchResponse:
    images = generate_product_images(query, per_page, base_url)
    return QuerySearchResponse(
        query=query,
        page=page,
        per_page=per_page,
        total_results=len(images),
        images=images,
    )


def collect_suggested_images(
    product_name: str,
    *,
    count: int = DEFAULT_SUGGESTED_IMAGE_COUNT,
    base_url: str,
) -> ProductImageSuggestResponse:
    cleaned_name = product_name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="product_name must not be blank")

    image_count = min(max(count, 1), MAX_GENERATED_IMAGES_PER_REQUEST)
    styles = [PRODUCT_IMAGE_STYLES[index % len(PRODUCT_IMAGE_STYLES)] for index in range(image_count)]
    images = generate_product_images(cleaned_name, image_count, base_url)
    return ProductImageSuggestResponse(
        product_name=cleaned_name,
        styles=styles[: len(images)],
        images=images,
    )


@router.get("", response_model=QuerySearchResponse)
@limiter.limit(RATE_LIMIT_AI)
def search_images(
    request: Request,
    current_user: AuthenticatedUser,
    query: str = Query(min_length=1, max_length=200),
    page: int = Query(default=1, ge=1, le=100),
    per_page: int = Query(default=10, ge=1, le=MAX_GENERATED_IMAGES_PER_REQUEST),
) -> QuerySearchResponse:
    require_active_plan(current_user)
    cleaned_query = query.strip()
    if not cleaned_query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="query must not be blank")
    return build_query_search_response(
        query=cleaned_query,
        page=page,
        per_page=per_page,
        base_url=request_base_url(request),
    )


@router.post("", response_model=QuerySearchResponse, status_code=status.HTTP_200_OK)
@limiter.limit(RATE_LIMIT_AI)
def search_images_post(
    request: Request,
    payload: QuerySearchRequest,
    current_user: AuthenticatedUser,
) -> QuerySearchResponse:
    require_active_plan(current_user)
    return build_query_search_response(
        query=payload.query,
        page=payload.page,
        per_page=payload.per_page,
        base_url=request_base_url(request),
    )


@router.post("/suggest-images", response_model=ProductImageSuggestResponse, status_code=status.HTTP_200_OK)
@limiter.limit(RATE_LIMIT_AI)
def suggest_product_images(
    request: Request,
    payload: ProductImageSuggestRequest,
    current_user: AuthenticatedUser,
) -> ProductImageSuggestResponse:
    require_active_plan(current_user)
    return collect_suggested_images(payload.product_name, base_url=request_base_url(request))
