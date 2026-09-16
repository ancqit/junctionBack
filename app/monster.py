"""Video shorts for junction.monster — Instagram/YouTube-style, Junction-bound.

Create studio lives on junction.monster. junction.today Promotion lists
shorts for a place and plays them in-feed (create still deep-links here).

Avoid `from __future__ import annotations`: with FastAPI/slowapi it turns
UploadFile into ForwardRef and crashes app startup (see catalog_otp.py).
"""

import re
import secrets
from datetime import datetime, timezone
from typing import Literal

from bson import ObjectId
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from gridfs import GridFS
from gridfs.errors import NoFile
from pydantic import BaseModel, Field, field_validator

from .access_control import require_store_access
from .database import database, monster_posts, shops
from .rate_limit import RATE_LIMIT_AUTH, RATE_LIMIT_CATALOG, limiter
from .session import CatalogReader, is_junction_session
from .utils import parse_object_id

router = APIRouter(prefix="/monster", tags=["monster"])

MonsterScope = Literal["locality", "city"]
MonsterAuthorKind = Literal["person", "shop"]

# Caption like IG/YT — short text beside the video, not a blog.
SHORT_CAPTION_MAX = 150
SHORT_TITLE_MAX = 48
SHORT_AUTHOR_MAX = 60
SHORT_DURATION_MAX_SEC = 30
SHORT_VIDEO_MAX_BYTES = 40 * 1024 * 1024  # masters OK; playback quality comes from delivery, not phone crush
SHORT_AUDIO_MAX_BYTES = 2 * 1024 * 1024
SHORT_POSTER_MAX_BYTES = 512 * 1024
SHORT_TTL_DAYS = 30
SHORT_TTL_SECONDS = SHORT_TTL_DAYS * 24 * 60 * 60

ALLOWED_VIDEO_TYPES = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}
ALLOWED_AUDIO_TYPES = {
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/x-m4a": ".m4a",
}
ALLOWED_POSTER_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

_WHITESPACE_RE = re.compile(r"\s+")
short_video_fs = GridFS(database, collection="monster_short_videos")
short_audio_fs = GridFS(database, collection="monster_short_audio")
short_poster_fs = GridFS(database, collection="monster_short_posters")


def _compact_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value).strip()


class MonsterVideoUpload(BaseModel):
    video_id: str
    content_type: str
    size_bytes: int
    filename: str


class MonsterAudioUpload(BaseModel):
    audio_id: str
    content_type: str
    size_bytes: int
    filename: str
    track_name: str = ""


class MonsterPosterUpload(BaseModel):
    poster_id: str
    content_type: str
    size_bytes: int
    filename: str


class MonsterPostCreate(BaseModel):
    """Publish a short after uploading video via POST /monster/shorts/video."""

    author_name: str = Field(min_length=2, max_length=SHORT_AUTHOR_MAX)
    title: str = Field(default="", max_length=SHORT_TITLE_MAX)
    caption: str = Field(default="", max_length=SHORT_CAPTION_MAX)
    # Legacy alias — prefer caption.
    body: str = Field(default="", max_length=SHORT_CAPTION_MAX)
    scope: MonsterScope = "locality"
    city: str = Field(min_length=1, max_length=80)
    locality: str | None = Field(default=None, max_length=120)
    author_kind: MonsterAuthorKind = "person"
    shop_id: str | None = Field(default=None, max_length=80)
    video_id: str = Field(min_length=1, max_length=80)
    audio_id: str | None = Field(default=None, max_length=80)
    poster_id: str | None = Field(default=None, max_length=80)
    track_name: str = Field(default="", max_length=80)
    duration_seconds: int = Field(default=15, ge=1, le=SHORT_DURATION_MAX_SEC)

    @field_validator(
        "author_name",
        "title",
        "caption",
        "body",
        "city",
        "locality",
        "shop_id",
        "video_id",
        "audio_id",
        "poster_id",
        "track_name",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return _compact_text(value)
        return value


class MonsterPost(BaseModel):
    id: str
    author_name: str
    title: str = ""
    caption: str = ""
    body: str = ""  # alias of caption for older clients
    scope: MonsterScope
    city: str
    locality: str | None = None
    author_kind: MonsterAuthorKind = "person"
    shop_id: str | None = None
    shop_name: str | None = None
    video_id: str
    video_url: str
    playback_url: str = ""
    poster_id: str | None = None
    poster_url: str | None = None
    status: Literal["ready", "processing", "failed"] = "ready"
    audio_id: str | None = None
    audio_url: str | None = None
    track_name: str = ""
    duration_seconds: int = 15
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
    monster_posts.create_index([("video_id", 1)])
    # TTL options are immutable — drop/recreate when SHORT_TTL_DAYS changes.
    ttl_name = "created_at_1"
    existing = next((idx for idx in monster_posts.list_indexes() if idx.get("name") == ttl_name), None)
    if existing is not None and existing.get("expireAfterSeconds") != SHORT_TTL_SECONDS:
        monster_posts.drop_index(ttl_name)
    monster_posts.create_index("created_at", expireAfterSeconds=SHORT_TTL_SECONDS)


def _video_url(video_id: str) -> str:
    return f"/monster/shorts/video/{video_id}"


def _audio_url(audio_id: str) -> str:
    return f"/monster/shorts/audio/{audio_id}"


def _poster_url(poster_id: str) -> str:
    return f"/monster/shorts/poster/{poster_id}"


def _parse_byte_range(range_header: str | None, length: int) -> tuple[int, int] | None:
    """Return inclusive (start, end) for a single bytes range, or None for full body."""
    if not range_header or length <= 0:
        return None
    match = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
    if not match:
        return None
    start_raw, end_raw = match.group(1), match.group(2)
    if start_raw == "" and end_raw == "":
        return None
    if start_raw == "":
        # suffix: last N bytes
        suffix = int(end_raw)
        if suffix <= 0:
            return None
        start = max(0, length - suffix)
        end = length - 1
        return start, end
    start = int(start_raw)
    end = int(end_raw) if end_raw else length - 1
    if start >= length:
        return None
    end = min(end, length - 1)
    if end < start:
        return None
    return start, end


def _stream_grid_file(request: Request, grid_out, *, default_media_type: str, default_filename: str) -> Response:
    length = int(getattr(grid_out, "length", 0) or 0)
    media_type = grid_out.content_type or default_media_type
    filename = grid_out.filename or default_filename
    base_headers = {
        "Content-Disposition": f'inline; filename="{filename}"',
        "Cache-Control": "public, max-age=86400",
        "Accept-Ranges": "bytes",
    }
    byte_range = _parse_byte_range(request.headers.get("range"), length)
    if byte_range is None:
        if length:
            base_headers["Content-Length"] = str(length)
        return StreamingResponse(grid_out, media_type=media_type, headers=base_headers)

    start, end = byte_range
    size = end - start + 1
    grid_out.seek(start)
    chunk = grid_out.read(size)
    return Response(
        content=chunk,
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=media_type,
        headers={
            **base_headers,
            "Content-Length": str(size),
            "Content-Range": f"bytes {start}-{end}/{length}",
        },
    )


def _require_place(scope: MonsterScope, city: str | None, locality: str | None) -> tuple[str, str | None]:
    city_name = (city or "").strip()
    locality_name = (locality or "").strip() or None
    if not city_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="city is required — shorts are Junction-bound")
    if scope == "city":
        return city_name, None
    if not locality_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="city and locality are required for Junction (locality) shorts",
        )
    return city_name, locality_name


def _resolve_shop(shop_id: str) -> dict:
    document = shops.find_one({"_id": parse_object_id(shop_id, "Shop")})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    return document


def _assert_video_exists(video_id: str) -> ObjectId:
    try:
        oid = ObjectId(video_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid video_id") from exc
    if not short_video_fs.exists(oid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found — upload or record on junction.monster first",
        )
    return oid


def _assert_audio_exists(audio_id: str) -> ObjectId:
    try:
        oid = ObjectId(audio_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid audio_id") from exc
    if not short_audio_fs.exists(oid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Track not found")
    return oid


def _assert_poster_exists(poster_id: str) -> ObjectId:
    try:
        oid = ObjectId(poster_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid poster_id") from exc
    if not short_poster_fs.exists(oid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Poster not found")
    return oid


def _serialize(document: dict) -> MonsterPost:
    author_kind = document.get("author_kind") or "person"
    shop_name = (str(document["shop_name"]).strip() or None) if document.get("shop_name") else None
    author_name = str(document.get("author_name") or "").strip()
    if not author_name and shop_name:
        author_name = shop_name
    caption = str(document.get("caption") or document.get("body") or "")
    video_id = str(document.get("video_id") or "")
    audio_id = (str(document["audio_id"]).strip() or None) if document.get("audio_id") else None
    poster_id = (str(document["poster_id"]).strip() or None) if document.get("poster_id") else None
    city = str(document.get("city") or "").strip()
    video_url = _video_url(video_id) if video_id else ""
    status_value = str(document.get("status") or "ready")
    if status_value not in {"ready", "processing", "failed"}:
        status_value = "ready"
    return MonsterPost(
        id=str(document["_id"]),
        author_name=author_name,
        title=str(document.get("title") or ""),
        caption=caption,
        body=caption,
        scope=document.get("scope") or "locality",
        city=city,
        locality=(str(document["locality"]).strip() or None) if document.get("locality") else None,
        author_kind=author_kind,
        shop_id=(str(document["shop_id"]).strip() or None) if document.get("shop_id") else None,
        shop_name=shop_name,
        video_id=video_id,
        video_url=video_url,
        playback_url=video_url,
        poster_id=poster_id,
        poster_url=_poster_url(poster_id) if poster_id else None,
        status=status_value,  # type: ignore[arg-type]
        audio_id=audio_id,
        audio_url=_audio_url(audio_id) if audio_id else None,
        track_name=str(document.get("track_name") or ""),
        duration_seconds=int(document.get("duration_seconds") or 15),
        created_at=document.get("created_at") or datetime.now(timezone.utc),
    )


def _create_short_document(payload: MonsterPostCreate, *, shop_doc: dict | None) -> dict:
    # Creates are Junction (locality) scoped — city/global are for viewing feeds only.
    scope: MonsterScope = "locality"
    city, locality = _require_place(scope, payload.city, payload.locality)
    if shop_doc is not None:
        shop_city = str(shop_doc.get("city") or "").strip()
        shop_locality = str(shop_doc.get("locality") or "").strip()
        city = city or shop_city
        locality = locality or shop_locality
        city, locality = _require_place("locality", city, locality)

    _assert_video_exists(payload.video_id)
    audio_id = (payload.audio_id or "").strip() or None
    if audio_id:
        _assert_audio_exists(audio_id)
    poster_id = (payload.poster_id or "").strip() or None
    if poster_id:
        _assert_poster_exists(poster_id)

    caption = payload.caption or payload.body
    now = datetime.now(timezone.utc)
    document: dict = {
        "caption": caption,
        "scope": scope,
        "city": city,
        "locality": locality,
        "author_kind": payload.author_kind,
        "video_id": payload.video_id,
        "duration_seconds": payload.duration_seconds,
        "status": "ready",
        "created_at": now,
    }
    if payload.title:
        document["title"] = payload.title
    if audio_id:
        document["audio_id"] = audio_id
    if poster_id:
        document["poster_id"] = poster_id
    if payload.track_name:
        document["track_name"] = payload.track_name

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


@router.post("/shorts/video", response_model=MonsterVideoUpload, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_AUTH)
async def upload_short_video(
    request: Request,
    auth: CatalogReader,
    file: UploadFile = File(...),
) -> MonsterVideoUpload:
    """Upload the video clip for a short (mp4/webm/mov, ≤30s intended, ≤40MB master)."""
    _ = auth
    contents = await file.read()
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_VIDEO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only MP4, WebM, and MOV videos are supported (like Instagram / YouTube Shorts)",
        )
    if not contents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded video is empty")
    if len(contents) > SHORT_VIDEO_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Video must be {SHORT_VIDEO_MAX_BYTES // (1024 * 1024)} MB or smaller",
        )

    filename = (file.filename or f"short{ALLOWED_VIDEO_TYPES[content_type]}").strip() or "short.mp4"
    file_id = short_video_fs.put(
        contents,
        content_type=content_type,
        filename=filename,
        metadata={"kind": "monster_short", "uploaded_at": datetime.now(timezone.utc).isoformat()},
    )
    return MonsterVideoUpload(
        video_id=str(file_id),
        content_type=content_type,
        size_bytes=len(contents),
        filename=filename,
    )


@router.post("/shorts/audio", response_model=MonsterAudioUpload, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_AUTH)
async def upload_short_audio(
    request: Request,
    auth: CatalogReader,
    file: UploadFile = File(...),
) -> MonsterAudioUpload:
    """Upload a mix track (mp3/m4a/wav/aac/webm) to layer under the short."""
    _ = auth
    contents = await file.read()
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only MP3, M4A, AAC, WAV, or WebM audio tracks are supported",
        )
    if not contents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded track is empty")
    if len(contents) > SHORT_AUDIO_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Track must be {SHORT_AUDIO_MAX_BYTES // (1024 * 1024)} MB or smaller",
        )

    filename = (file.filename or f"track{ALLOWED_AUDIO_TYPES[content_type]}").strip() or "track.mp3"
    track_name = filename.rsplit(".", 1)[0][:80]
    file_id = short_audio_fs.put(
        contents,
        content_type=content_type,
        filename=filename,
        metadata={"kind": "monster_short_track", "uploaded_at": datetime.now(timezone.utc).isoformat()},
    )
    return MonsterAudioUpload(
        audio_id=str(file_id),
        content_type=content_type,
        size_bytes=len(contents),
        filename=filename,
        track_name=track_name,
    )


@router.post("/shorts/poster", response_model=MonsterPosterUpload, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_AUTH)
async def upload_short_poster(
    request: Request,
    auth: CatalogReader,
    file: UploadFile = File(...),
) -> MonsterPosterUpload:
    """Upload a JPEG/PNG/WebP poster frame for in-feed instant paint."""
    _ = auth
    contents = await file.read()
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_POSTER_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only JPEG, PNG, or WebP posters are supported",
        )
    if not contents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded poster is empty")
    if len(contents) > SHORT_POSTER_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Poster must be {SHORT_POSTER_MAX_BYTES // 1024} KB or smaller",
        )

    filename = (file.filename or f"poster{ALLOWED_POSTER_TYPES[content_type]}").strip() or "poster.jpg"
    file_id = short_poster_fs.put(
        contents,
        content_type=content_type,
        filename=filename,
        metadata={"kind": "monster_short_poster", "uploaded_at": datetime.now(timezone.utc).isoformat()},
    )
    return MonsterPosterUpload(
        poster_id=str(file_id),
        content_type=content_type,
        size_bytes=len(contents),
        filename=filename,
    )


@router.get("/shorts/video/{video_id}")
@router.get("/posts/video/{video_id}")
@limiter.limit(RATE_LIMIT_CATALOG)
def stream_short_video(request: Request, video_id: str) -> Response:
    """Public stream with byte-Range so feed players can start immediately."""
    try:
        oid = ObjectId(video_id)
        grid_out = short_video_fs.get(oid)
    except (NoFile, Exception) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found") from exc

    return _stream_grid_file(
        request,
        grid_out,
        default_media_type="video/mp4",
        default_filename="short.mp4",
    )


@router.get("/shorts/poster/{poster_id}")
@limiter.limit(RATE_LIMIT_CATALOG)
def stream_short_poster(request: Request, poster_id: str) -> Response:
    """Public poster image for feed cards."""
    try:
        oid = ObjectId(poster_id)
        grid_out = short_poster_fs.get(oid)
    except (NoFile, Exception) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Poster not found") from exc

    return _stream_grid_file(
        request,
        grid_out,
        default_media_type="image/jpeg",
        default_filename="poster.jpg",
    )


@router.get("/shorts/audio/{audio_id}")
@limiter.limit(RATE_LIMIT_CATALOG)
def stream_short_audio(request: Request, audio_id: str) -> Response:
    """Public stream for mix tracks under a short."""
    try:
        oid = ObjectId(audio_id)
        grid_out = short_audio_fs.get(oid)
    except (NoFile, Exception) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Track not found") from exc

    return _stream_grid_file(
        request,
        grid_out,
        default_media_type="audio/mpeg",
        default_filename="track.mp3",
    )


@router.get("/posts", response_model=MonsterPostList)
@router.get("/shorts", response_model=MonsterPostList)
@limiter.limit(RATE_LIMIT_CATALOG)
def list_monster_posts(
    request: Request,
    _: CatalogReader,
    scope: MonsterScope | Literal["global"] | None = Query(default=None),
    city: str | None = Query(default=None, max_length=80),
    locality: str | None = Query(default=None, max_length=120),
    shop_id: str | None = Query(default=None, max_length=80),
    author_kind: MonsterAuthorKind | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> MonsterPostList:
    """List shorts. View layers: locality, city (all localities), or global (all places)."""
    _ensure_indexes()
    query: dict = {
        "video_id": {"$exists": True, "$ne": ""},
        "$or": [{"status": "ready"}, {"status": {"$exists": False}}],
    }
    city_name: str | None = None
    locality_name: str | None = None
    resolved_scope: MonsterScope | None = None

    if shop_id and shop_id.strip():
        query["shop_id"] = shop_id.strip()
    if author_kind:
        query["author_kind"] = author_kind

    if scope == "global":
        resolved_scope = None
    elif scope == "city":
        # City view: every locality short in that city.
        city_name, _ = _require_place("city", city, None)
        query["city"] = {"$regex": f"^{re.escape(city_name)}$", "$options": "i"}
        resolved_scope = None
    elif scope == "locality":
        resolved_scope = "locality"
        city_name, locality_name = _require_place("locality", city, locality)
        query["scope"] = "locality"
        query["city"] = {"$regex": f"^{re.escape(city_name)}$", "$options": "i"}
        query["locality"] = {"$regex": f"^{re.escape(locality_name or '')}$", "$options": "i"}
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
    if document is None or not document.get("video_id"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Short not found")
    return _serialize(document)


class MonsterPostCreated(MonsterPost):
    """Create response — includes a one-time delete_token for the publisher."""

    delete_token: str


class MonsterPostDelete(BaseModel):
    delete_token: str = Field(min_length=8, max_length=120)


@router.post("/posts", response_model=MonsterPostCreated, status_code=status.HTTP_201_CREATED)
@router.post("/shorts", response_model=MonsterPostCreated, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_AUTH)
def create_monster_post(
    request: Request,
    payload: MonsterPostCreate,
    auth: CatalogReader,
) -> MonsterPostCreated:
    """
    Publish a Junction (locality) video short.

    Upload the clip first via POST /monster/shorts/video (junction.monster create studio).
    Optional mix track via POST /monster/shorts/audio.
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
    delete_token = secrets.token_urlsafe(24)
    document["delete_token"] = delete_token
    result = monster_posts.insert_one(document)
    document["_id"] = result.inserted_id
    created = _serialize(document)
    return MonsterPostCreated(**created.model_dump(), delete_token=delete_token)


@router.delete("/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/shorts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(RATE_LIMIT_AUTH)
def delete_monster_post(
    request: Request,
    post_id: str,
    payload: MonsterPostDelete,
    _: CatalogReader,
) -> Response:
    """Delete a short using the delete_token returned at publish time."""
    _ = request
    document = monster_posts.find_one({"_id": parse_object_id(post_id, "Post")})
    if document is None or not document.get("video_id"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Short not found")
    stored = str(document.get("delete_token") or "")
    if not stored or not secrets.compare_digest(stored, payload.delete_token.strip()):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to delete this short")
    purge_short_document(document)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def purge_short_document(document: dict) -> str:
    """Remove a short row and its GridFS blobs. Returns the post id."""
    post_id = str(document["_id"])
    video_id = str(document.get("video_id") or "").strip()
    audio_id = str(document.get("audio_id") or "").strip()
    poster_id = str(document.get("poster_id") or "").strip()
    if video_id and ObjectId.is_valid(video_id):
        try:
            short_video_fs.delete(ObjectId(video_id))
        except Exception:
            pass
    if audio_id and ObjectId.is_valid(audio_id):
        try:
            short_audio_fs.delete(ObjectId(audio_id))
        except Exception:
            pass
    if poster_id and ObjectId.is_valid(poster_id):
        try:
            short_poster_fs.delete(ObjectId(poster_id))
        except Exception:
            pass
    monster_posts.delete_one({"_id": document["_id"]})
    return post_id


def admin_delete_short(post_id: str) -> dict:
    """Admin path — delete by id without a publish delete_token."""
    document = monster_posts.find_one({"_id": parse_object_id(post_id, "Post")})
    if document is None or not document.get("video_id"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Short not found")
    caption = str(document.get("caption") or document.get("body") or "")
    author = str(document.get("shop_name") or document.get("author_name") or "")
    purge_short_document(document)
    return {
        "deleted": True,
        "post_id": post_id,
        "author_name": author,
        "caption": caption[:120],
    }
