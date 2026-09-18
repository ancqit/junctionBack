"""Video shorts for junction.monster — Instagram/YouTube-style, Junction-bound.

Create studio lives on junction.monster. junction.today Promotion lists
shorts for a place and plays them in-feed (create still deep-links here).

Avoid `from __future__ import annotations`: with FastAPI/slowapi it turns
UploadFile into ForwardRef and crashes app startup (see catalog_otp.py).
"""

import os
import re
import secrets
from datetime import datetime, timezone
from typing import Literal

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from gridfs import GridFS
from gridfs.errors import NoFile
from pydantic import BaseModel, Field, field_validator, model_validator

from .access_control import require_store_access
from .database import database, monster_posts, shops
from .rate_limit import RATE_LIMIT_AUTH, RATE_LIMIT_CATALOG, RATE_LIMIT_MEDIA, limiter
from . import cf_stream
from . import r2_media
from . import short_playback
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
# Cap each Range response so first playable bytes leave the server immediately
# (browsers re-request the next window). Avoids buffering a full 40MB master.
SHORT_RANGE_MAX_BYTES = 1 * 1024 * 1024
SHORT_STREAM_CHUNK_BYTES = 256 * 1024

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


class MonsterSignRequest(BaseModel):
    content_type: str = Field(min_length=3, max_length=80)
    filename: str = Field(default="", max_length=120)


class MonsterSignResponse(BaseModel):
    kind: str
    object_key: str = ""
    upload_url: str
    public_url: str = ""
    content_type: str = ""
    expires_in: int
    max_bytes: int
    # Compat aliases so older clients can treat the key like an upload id.
    video_id: str = ""
    audio_id: str = ""
    poster_id: str = ""
    # Cloudflare Stream direct upload (multipart POST) when configured.
    provider: Literal["r2", "stream", "gridfs"] = "r2"
    upload_method: Literal["PUT", "POST"] = "PUT"
    stream_uid: str = ""
    hls_url: str = ""
    thumbnail_url: str = ""


class MonsterPostCreate(BaseModel):
    """Publish a short after uploading video (R2 sign+PUT or legacy GridFS upload)."""

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
    video_id: str = Field(default="", max_length=80)
    video_key: str = Field(default="", max_length=200)
    stream_uid: str = Field(default="", max_length=80)
    audio_id: str | None = Field(default=None, max_length=80)
    audio_key: str | None = Field(default=None, max_length=200)
    poster_id: str | None = Field(default=None, max_length=80)
    poster_key: str | None = Field(default=None, max_length=200)
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
        "video_key",
        "stream_uid",
        "audio_id",
        "audio_key",
        "poster_id",
        "poster_key",
        "track_name",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return _compact_text(value)
        return value

    @model_validator(mode="after")
    def require_video_ref(self) -> "MonsterPostCreate":
        if not (self.video_key or self.video_id or self.stream_uid):
            raise ValueError("video_key, video_id, or stream_uid is required")
        return self


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
    video_id: str = ""
    video_key: str = ""
    stream_uid: str = ""
    video_url: str
    playback_url: str = ""
    download_url: str = ""
    poster_id: str | None = None
    poster_key: str | None = None
    poster_url: str | None = None
    status: Literal["ready", "processing", "failed"] = "ready"
    audio_id: str | None = None
    audio_key: str | None = None
    audio_url: str | None = None
    track_name: str = ""
    duration_seconds: int = 15
    created_at: datetime
    hls_url: str = ""


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


def _download_url(post_id: str) -> str:
    return f"/monster/shorts/{post_id}/download"


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


def _iter_grid_bytes(grid_out, start: int, size: int):
    """Yield GridFS bytes without loading the whole window into memory."""
    grid_out.seek(start)
    remaining = max(0, size)
    while remaining > 0:
        chunk = grid_out.read(min(SHORT_STREAM_CHUNK_BYTES, remaining))
        if not chunk:
            break
        remaining -= len(chunk)
        yield chunk


def _stream_grid_file(
    request: Request,
    grid_out,
    *,
    default_media_type: str,
    default_filename: str,
    disposition: Literal["inline", "attachment"] = "inline",
    cap_range: bool = True,
) -> Response:
    length = int(getattr(grid_out, "length", 0) or 0)
    media_type = grid_out.content_type or default_media_type
    filename = grid_out.filename or default_filename
    base_headers = {
        "Content-Disposition": f'{disposition}; filename="{filename}"',
        "Cache-Control": "public, max-age=86400, immutable",
        "Accept-Ranges": "bytes",
        "X-Content-Type-Options": "nosniff",
    }
    byte_range = _parse_byte_range(request.headers.get("range"), length)

    # Full-body responses: stream in chunks (never buffer the whole GridFS file).
    if byte_range is None:
        if length:
            base_headers["Content-Length"] = str(length)
        return StreamingResponse(
            _iter_grid_bytes(grid_out, 0, length) if length else grid_out,
            media_type=media_type,
            headers=base_headers,
        )

    start, end = byte_range
    if cap_range and (end - start + 1) > SHORT_RANGE_MAX_BYTES:
        end = start + SHORT_RANGE_MAX_BYTES - 1
    size = end - start + 1
    return StreamingResponse(
        _iter_grid_bytes(grid_out, start, size),
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


def _has_video(document: dict | None) -> bool:
    if not document:
        return False
    if str(document.get("stream_uid") or "").strip():
        return True
    if str(document.get("video_key") or "").strip():
        return True
    return bool(str(document.get("video_id") or "").strip())


def _serialize(document: dict) -> MonsterPost:
    author_kind = document.get("author_kind") or "person"
    shop_name = (str(document["shop_name"]).strip() or None) if document.get("shop_name") else None
    author_name = str(document.get("author_name") or "").strip()
    if not author_name and shop_name:
        author_name = shop_name
    caption = str(document.get("caption") or document.get("body") or "")
    video_id = str(document.get("video_id") or "")
    video_key = str(document.get("video_key") or "").strip()
    stream_uid = str(document.get("stream_uid") or "").strip()
    playback_key = str(document.get("playback_key") or "").strip()
    playback_video_id = str(document.get("playback_video_id") or "").strip()
    audio_id = (str(document["audio_id"]).strip() or None) if document.get("audio_id") else None
    audio_key = (str(document["audio_key"]).strip() or None) if document.get("audio_key") else None
    poster_id = (str(document["poster_id"]).strip() or None) if document.get("poster_id") else None
    poster_key = (str(document["poster_key"]).strip() or None) if document.get("poster_key") else None
    city = str(document.get("city") or "").strip()

    hls = ""
    if stream_uid and cf_stream.stream_configured():
        hls = str(document.get("stream_hls_url") or "").strip() or cf_stream.hls_url(stream_uid)
        video_url = hls
    elif playback_key:
        video_url = r2_media.public_url(playback_key)
    elif video_key:
        video_url = r2_media.public_url(video_key)
    else:
        play_id = playback_video_id or video_id
        video_url = _video_url(play_id) if play_id else ""

    if poster_key:
        poster_url = r2_media.public_url(poster_key)
    elif poster_id:
        poster_url = _poster_url(poster_id)
    elif stream_uid and cf_stream.stream_configured():
        poster_url = (
            str(document.get("stream_thumbnail_url") or "").strip()
            or cf_stream.thumbnail_url(stream_uid)
            or None
        )
    else:
        poster_url = None

    if audio_key:
        audio_url = r2_media.public_url(audio_key)
    elif audio_id:
        audio_url = _audio_url(audio_id)
    else:
        audio_url = None

    status_value = str(document.get("status") or "ready")
    if status_value not in {"ready", "processing", "failed"}:
        status_value = "ready"
    has_media = bool(stream_uid or video_key or video_id)
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
        video_id=video_id or video_key or stream_uid,
        video_key=video_key,
        stream_uid=stream_uid,
        video_url=video_url,
        playback_url=video_url,
        download_url=_download_url(str(document["_id"])) if has_media else "",
        poster_id=poster_id,
        poster_key=poster_key,
        poster_url=poster_url,
        status=status_value,  # type: ignore[arg-type]
        audio_id=audio_id,
        audio_key=audio_key,
        audio_url=audio_url,
        track_name=str(document.get("track_name") or ""),
        duration_seconds=int(document.get("duration_seconds") or 15),
        created_at=document.get("created_at") or datetime.now(timezone.utc),
        hls_url=hls,
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

    video_key = (payload.video_key or "").strip()
    video_id = (payload.video_id or "").strip()
    stream_uid = (payload.stream_uid or "").strip()
    if stream_uid:
        if not cf_stream.stream_configured():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="stream_uid provided but Cloudflare Stream is not configured",
            )
        video_key = ""
        video_id = ""
    elif video_key:
        video_key = r2_media.assert_owned_key(video_key)
        video_id = ""
    elif video_id:
        _assert_video_exists(video_id)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="video_key, video_id, or stream_uid is required",
        )

    audio_key = (payload.audio_key or "").strip() or None
    audio_id = (payload.audio_id or "").strip() or None
    if audio_key:
        audio_key = r2_media.assert_owned_key(audio_key)
        audio_id = None
    elif audio_id:
        _assert_audio_exists(audio_id)

    poster_key = (payload.poster_key or "").strip() or None
    poster_id = (payload.poster_id or "").strip() or None
    if poster_key:
        poster_key = r2_media.assert_owned_key(poster_key)
        poster_id = None
    elif poster_id:
        _assert_poster_exists(poster_id)

    caption = payload.caption or payload.body
    now = datetime.now(timezone.utc)
    document: dict = {
        "caption": caption,
        "scope": scope,
        "city": city,
        "locality": locality,
        "author_kind": payload.author_kind,
        "duration_seconds": payload.duration_seconds,
        "status": "processing" if stream_uid else "ready",
        "created_at": now,
    }
    if stream_uid:
        document["stream_uid"] = stream_uid
    if video_key:
        document["video_key"] = video_key
    if video_id:
        document["video_id"] = video_id
    if payload.title:
        document["title"] = payload.title
    if audio_key:
        document["audio_key"] = audio_key
    if audio_id:
        document["audio_id"] = audio_id
    if poster_key:
        document["poster_key"] = poster_key
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


@router.post("/shorts/video/sign", response_model=MonsterSignResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def sign_short_video_upload(
    request: Request,
    payload: MonsterSignRequest,
    auth: CatalogReader,
) -> MonsterSignResponse:
    """Mint a short-lived upload URL.

    Default: R2 PUT (stable through Cloudflare). Stream ABR is opt-in via
    STREAM_PREFERRED=1 — Stream-first uploads were returning incomplete
    origin responses for many clients.
    """
    _ = request, auth
    prefer_stream = (os.getenv("STREAM_PREFERRED") or "").strip().lower() in ("1", "true", "yes")
    if prefer_stream and cf_stream.stream_configured():
        signed = cf_stream.create_direct_upload(max_duration_seconds=SHORT_DURATION_MAX_SEC)
        return MonsterSignResponse(
            kind="video",
            object_key=signed["uid"],
            upload_url=signed["upload_url"],
            public_url=signed["hls_url"],
            content_type=payload.content_type or "video/mp4",
            expires_in=signed["expires_in"],
            max_bytes=SHORT_VIDEO_MAX_BYTES,
            video_id=signed["uid"],
            provider="stream",
            upload_method="POST",
            stream_uid=signed["uid"],
            hls_url=signed["hls_url"],
            thumbnail_url=signed["thumbnail_url"],
        )
    if not r2_media.r2_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="R2 is not configured — set R2_* env (or STREAM_PREFERRED=1 for Stream)",
        )
    signed = r2_media.presign_put(
        kind="video",
        content_type=payload.content_type,
        filename=payload.filename,
    )
    return MonsterSignResponse(
        **signed,
        max_bytes=SHORT_VIDEO_MAX_BYTES,
        video_id=signed["object_key"],
        provider="r2",
        upload_method="PUT",
    )


@router.post("/shorts/audio/sign", response_model=MonsterSignResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def sign_short_audio_upload(
    request: Request,
    payload: MonsterSignRequest,
    auth: CatalogReader,
) -> MonsterSignResponse:
    _ = request, auth
    signed = r2_media.presign_put(
        kind="audio",
        content_type=payload.content_type,
        filename=payload.filename,
    )
    return MonsterSignResponse(
        **signed,
        max_bytes=SHORT_AUDIO_MAX_BYTES,
        audio_id=signed["object_key"],
    )


@router.post("/shorts/poster/sign", response_model=MonsterSignResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def sign_short_poster_upload(
    request: Request,
    payload: MonsterSignRequest,
    auth: CatalogReader,
) -> MonsterSignResponse:
    _ = request, auth
    signed = r2_media.presign_put(
        kind="poster",
        content_type=payload.content_type,
        filename=payload.filename,
    )
    return MonsterSignResponse(
        **signed,
        max_bytes=SHORT_POSTER_MAX_BYTES,
        poster_id=signed["object_key"],
    )


@router.post("/shorts/video", response_model=MonsterVideoUpload, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_AUTH)
async def upload_short_video(
    request: Request,
    auth: CatalogReader,
    file: UploadFile = File(...),
) -> MonsterVideoUpload:
    """Legacy GridFS upload — prefer POST /monster/shorts/video/sign when R2 is configured."""
    _ = auth
    if r2_media.r2_configured():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use POST /monster/shorts/video/sign and PUT the file to upload_url",
        )
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
    """Legacy GridFS mix upload — prefer /shorts/audio/sign when R2 is configured."""
    _ = auth
    if r2_media.r2_configured():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use POST /monster/shorts/audio/sign and PUT the file to upload_url",
        )
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
    """Legacy GridFS poster upload — prefer /shorts/poster/sign when R2 is configured."""
    _ = auth
    if r2_media.r2_configured():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use POST /monster/shorts/poster/sign and PUT the file to upload_url",
        )
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
@limiter.limit(RATE_LIMIT_MEDIA)
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


@router.get("/shorts/{post_id}/download")
@limiter.limit(RATE_LIMIT_MEDIA)
def download_short_video(request: Request, post_id: str) -> Response:
    """Public download of the short master (attachment) so creators can keep a copy before delete."""
    document = monster_posts.find_one({"_id": parse_object_id(post_id, "Post")})
    if document is None or not _has_video(document):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Short not found")

    video_key = str(document.get("video_key") or "").strip()
    if video_key:
        # Browser downloads from the CDN host (no Render bandwidth).
        return RedirectResponse(url=r2_media.public_url(video_key), status_code=status.HTTP_302_FOUND)

    video_id = str(document.get("video_id") or "").strip()
    try:
        grid_out = short_video_fs.get(ObjectId(video_id))
    except (NoFile, Exception) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found") from exc

    ext = ALLOWED_VIDEO_TYPES.get(grid_out.content_type or "", ".mp4")
    return _stream_grid_file(
        request,
        grid_out,
        default_media_type="video/mp4",
        default_filename=f"junction-short-{post_id}{ext}",
        disposition="attachment",
        cap_range=False,
    )


@router.get("/shorts/poster/{poster_id}")
@limiter.limit(RATE_LIMIT_MEDIA)
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
        cap_range=False,
    )


@router.get("/shorts/audio/{audio_id}")
@limiter.limit(RATE_LIMIT_MEDIA)
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
        "$and": [
            {
                "$or": [
                    {"video_key": {"$exists": True, "$nin": [None, ""]}},
                    {"video_id": {"$exists": True, "$ne": ""}},
                ]
            },
            {"$or": [{"status": "ready"}, {"status": {"$exists": False}}]},
        ]
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
    if document is None or not _has_video(document):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Short not found")
    return _serialize(document)


class MonsterPostCreated(MonsterPost):
    """Create response — includes a one-time delete_token for later profile-bound reclaim."""

    delete_token: str


@router.post("/posts", response_model=MonsterPostCreated, status_code=status.HTTP_201_CREATED)
@router.post("/shorts", response_model=MonsterPostCreated, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_AUTH)
def create_monster_post(
    request: Request,
    payload: MonsterPostCreate,
    auth: CatalogReader,
    background_tasks: BackgroundTasks,
) -> MonsterPostCreated:
    """
    Publish a Junction (locality) video short.

    Upload the clip first via POST /monster/shorts/video/sign (R2) or legacy /shorts/video.
    Optional mix track via /shorts/audio/sign; poster via /shorts/poster/sign.
    After create, a background job encodes an H.264 `playback_key` for HD feed play.
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
    post_id = str(result.inserted_id)
    stream_uid = str(document.get("stream_uid") or "").strip()
    if stream_uid:
        # Cloudflare Stream packages ABR (360p–1080p) — poll until readyToStream.
        background_tasks.add_task(cf_stream.wait_until_ready_for_post, post_id, stream_uid)
    else:
        # R2/GridFS path: encode a dedicated H.264 playback_key for HD feed play.
        background_tasks.add_task(short_playback.ensure_playback_for_post, post_id)
    created = _serialize(document)
    return MonsterPostCreated(**created.model_dump(), delete_token=delete_token)


@router.delete("/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/shorts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(RATE_LIMIT_AUTH)
def delete_monster_post(
    request: Request,
    post_id: str,
    _: CatalogReader,
) -> Response:
    """Interim open delete by id — anyone with a catalog session can remove any short.

    Later: bind delete to profile ownership (phone/shop) and stop open deletes.
    """
    _ = request
    document = monster_posts.find_one({"_id": parse_object_id(post_id, "Post")})
    if document is None or not _has_video(document):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Short not found")
    purge_short_document(document)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def purge_short_document(document: dict) -> str:
    """Remove a short row and its Stream / R2 / GridFS blobs. Returns the post id."""
    post_id = str(document["_id"])
    cf_stream.delete_video(str(document.get("stream_uid") or ""))
    for key_field in ("video_key", "playback_key", "audio_key", "poster_key"):
        r2_media.delete_object(str(document.get(key_field) or ""))
    video_id = str(document.get("video_id") or "").strip()
    playback_video_id = str(document.get("playback_video_id") or "").strip()
    audio_id = str(document.get("audio_id") or "").strip()
    poster_id = str(document.get("poster_id") or "").strip()
    for blob_id in {video_id, playback_video_id}:
        if blob_id and ObjectId.is_valid(blob_id):
            try:
                short_video_fs.delete(ObjectId(blob_id))
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
    if document is None or not _has_video(document):
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
