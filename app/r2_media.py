"""Cloudflare R2 helpers for Junction shorts (S3-compatible, CDN public URLs)."""

from __future__ import annotations

import os
import re
import uuid
from functools import lru_cache
from typing import Literal

import boto3
from botocore.client import Config
from fastapi import HTTPException, status

MediaKind = Literal["video", "audio", "poster"]

R2_ACCOUNT_ID = (os.getenv("R2_ACCOUNT_ID") or "").strip()
R2_ACCESS_KEY_ID = (os.getenv("R2_ACCESS_KEY_ID") or "").strip()
R2_SECRET_ACCESS_KEY = (os.getenv("R2_SECRET_ACCESS_KEY") or "").strip()
R2_BUCKET = (os.getenv("R2_BUCKET") or "").strip()
R2_PUBLIC_BASE_URL = (os.getenv("R2_PUBLIC_BASE_URL") or "").rstrip("/")
R2_ENDPOINT = (os.getenv("R2_ENDPOINT") or "").strip()
R2_SIGN_EXPIRES_SECONDS = int(os.getenv("R2_SIGN_EXPIRES_SECONDS") or "600")

_KEY_RE = re.compile(r"^shorts/[a-zA-Z0-9_-]+\.(mp4|webm|mov|m4a|mp3|wav|aac|jpg|jpeg|png|webp)$")

_KIND_EXTS = {
    "video": {
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/quicktime": ".mov",
    },
    "audio": {
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/aac": ".aac",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/webm": ".webm",
    },
    "poster": {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    },
}

_CONTENT_TYPE_ALIASES = {
    "video/x-matroska": "video/webm",
    "video/mkv": "video/webm",
    "application/mp4": "video/mp4",
    "audio/mp3": "audio/mpeg",
    "audio/x-m4a": "audio/mp4",
    "image/jpg": "image/jpeg",
}


def normalize_content_type(kind: MediaKind, content_type: str, filename: str = "") -> str:
    """Map aliases / empty browser types onto the canonical R2 Content-Type."""
    raw = (content_type or "").split(";")[0].strip().lower()
    if raw in _CONTENT_TYPE_ALIASES:
        raw = _CONTENT_TYPE_ALIASES[raw]
    if raw in _KIND_EXTS[kind]:
        return raw
    name = (filename or "").strip().lower()
    if kind == "video":
        if name.endswith(".webm"):
            return "video/webm"
        if name.endswith(".mov"):
            return "video/quicktime"
        if name.endswith(".mp4") or name.endswith(".m4v"):
            return "video/mp4"
    if kind == "audio":
        if name.endswith(".mp3"):
            return "audio/mpeg"
        if name.endswith(".m4a"):
            return "audio/mp4"
        if name.endswith(".aac"):
            return "audio/aac"
        if name.endswith(".wav"):
            return "audio/wav"
        if name.endswith(".webm"):
            return "audio/webm"
    if kind == "poster":
        if name.endswith(".png"):
            return "image/png"
        if name.endswith(".webp"):
            return "image/webp"
        if name.endswith(".jpg") or name.endswith(".jpeg"):
            return "image/jpeg"
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported {kind} content type",
    )


def r2_configured() -> bool:
    return bool(
        R2_ACCOUNT_ID
        and R2_ACCESS_KEY_ID
        and R2_SECRET_ACCESS_KEY
        and R2_BUCKET
        and R2_PUBLIC_BASE_URL
        and (R2_ENDPOINT or R2_ACCOUNT_ID)
    )


def require_r2() -> None:
    if not r2_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="R2 media storage is not configured on this server",
        )


def _endpoint() -> str:
    if R2_ENDPOINT:
        return R2_ENDPOINT
    return f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"


@lru_cache(maxsize=1)
def _client():
    require_r2()
    return boto3.client(
        "s3",
        endpoint_url=_endpoint(),
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def public_url(object_key: str) -> str:
    key = (object_key or "").lstrip("/")
    return f"{R2_PUBLIC_BASE_URL}/{key}"


def is_valid_object_key(object_key: str) -> bool:
    return bool(_KEY_RE.match((object_key or "").strip()))


def new_object_key(kind: MediaKind, content_type: str) -> str:
    mapping = _KIND_EXTS[kind]
    ext = mapping.get(content_type)
    if not ext:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported {kind} content type",
        )
    token = uuid.uuid4().hex
    if kind == "audio":
        return f"shorts/{token}-audio{ext}"
    if kind == "poster":
        return f"shorts/{token}-poster{ext}"
    return f"shorts/{token}{ext}"


def presign_put(*, kind: MediaKind, content_type: str, filename: str = "") -> dict:
    require_r2()
    content_type = normalize_content_type(kind, content_type, filename)
    object_key = new_object_key(kind, content_type)
    upload_url = _client().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": R2_BUCKET,
            "Key": object_key,
            "ContentType": content_type,
        },
        ExpiresIn=R2_SIGN_EXPIRES_SECONDS,
    )
    return {
        "object_key": object_key,
        "upload_url": upload_url,
        "public_url": public_url(object_key),
        "content_type": content_type,
        "expires_in": R2_SIGN_EXPIRES_SECONDS,
        "kind": kind,
    }


def delete_object(object_key: str | None) -> None:
    key = (object_key or "").strip()
    if not key or not r2_configured():
        return
    try:
        _client().delete_object(Bucket=R2_BUCKET, Key=key)
    except Exception:
        pass


def assert_owned_key(object_key: str) -> str:
    key = (object_key or "").strip()
    if not is_valid_object_key(key):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid media object key")
    return key
