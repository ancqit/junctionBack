"""Public, stateless QR poster generator for junction.today and Junction Front Web."""

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from .qr_brand import (
    BRAND_NAME,
    LOGO_SVG,
    TAGLINES,
    build_junction_url,
    compose_poster,
    poster_filename,
    resolve_taglines,
)
from .rate_limit import RATE_LIMIT_QR, limiter

router = APIRouter(prefix="/qr", tags=["qr"])


class QrLineOut(BaseModel):
    id: str
    kind: str
    en: str
    hi: str


class TaglineCatalog(BaseModel):
    brand_name: str = BRAND_NAME
    taglines: list[QrLineOut]


class QrGenerateRequest(BaseModel):
    city: str = Field(min_length=1, max_length=80)
    locality: str | None = Field(default=None, max_length=80)
    shop_name: str | None = Field(default=None, max_length=120)
    store_id: str | None = Field(default=None, max_length=80)
    tagline_id: str | None = Field(default=None, max_length=40)
    saying_ids: list[str] = Field(default_factory=list, max_length=3)
    custom_slogan: str | None = Field(default=None, max_length=160)
    lang: str = Field(default="en", max_length=8)


@router.get("/taglines", response_model=TaglineCatalog)
def list_taglines() -> TaglineCatalog:
    """Catchy taglines, sayings, and thoughts for branded Junction QR posters."""
    return TaglineCatalog(taglines=[QrLineOut(id=line.id, kind=line.kind, en=line.en, hi=line.hi) for line in TAGLINES])


@router.get("/logo.svg")
def junction_logo() -> Response:
    return Response(content=LOGO_SVG, media_type="image/svg+xml")


def _poster_response(payload: QrGenerateRequest) -> Response:
    city = payload.city.strip()
    locality = (payload.locality or "").strip() or None
    shop_name = (payload.shop_name or "").strip() or None
    store_id = (payload.store_id or "").strip() or None
    lines = resolve_taglines(
        payload.saying_ids,
        fallback=payload.tagline_id,
        custom_slogan=payload.custom_slogan,
    )
    url = build_junction_url(city=city, locality=locality, shop_name=shop_name, store_id=store_id)
    place = f"{locality}, {city}" if locality else city
    png = compose_poster(
        payload=url,
        junction_label=place,
        shop_name=shop_name,
        lines=lines,
        lang=payload.lang,
    )
    filename = poster_filename(city=city, locality=locality, shop_name=shop_name)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/generate")
@limiter.limit(RATE_LIMIT_QR)
def generate_qr(request: Request, payload: QrGenerateRequest) -> Response:
    """Build a branded PNG poster in memory and return it for download. Nothing is stored."""
    return _poster_response(payload)


@router.get("/generate")
@limiter.limit(RATE_LIMIT_QR)
def generate_qr_get(
    request: Request,
    city: str = Query(min_length=1, max_length=80),
    locality: str | None = Query(default=None, max_length=80),
    shop_name: str | None = Query(default=None, max_length=120),
    store_id: str | None = Query(default=None, max_length=80),
    tagline_id: str | None = Query(default=None, max_length=40),
    lang: str = Query(default="en", max_length=8),
) -> Response:
    return _poster_response(
        QrGenerateRequest(
            city=city,
            locality=locality,
            shop_name=shop_name,
            store_id=store_id,
            tagline_id=tagline_id,
            lang=lang,
        ),
    )
