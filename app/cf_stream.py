"""Cloudflare Stream — managed ABR video (closest to YouTube/Instagram delivery).

When configured, shorts upload via Direct Creator Upload and play from HLS
(`…/manifest/video.m3u8`). R2 remains for audio/posters and as video fallback.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

STREAM_ACCOUNT_ID = (os.getenv("STREAM_ACCOUNT_ID") or os.getenv("R2_ACCOUNT_ID") or "").strip()
STREAM_API_TOKEN = (os.getenv("STREAM_API_TOKEN") or "").strip()
# From dashboard: customer-<CODE>.cloudflarestream.com
STREAM_CUSTOMER_CODE = (os.getenv("STREAM_CUSTOMER_CODE") or "").strip()
STREAM_MAX_DURATION_SECONDS = int(os.getenv("STREAM_MAX_DURATION_SECONDS") or "30")
STREAM_UPLOAD_EXPIRY_SECONDS = int(os.getenv("STREAM_UPLOAD_EXPIRY_SECONDS") or "600")

_API = "https://api.cloudflare.com/client/v4"


def stream_configured() -> bool:
    return bool(STREAM_ACCOUNT_ID and STREAM_API_TOKEN and STREAM_CUSTOMER_CODE)


def require_stream() -> None:
    if not stream_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cloudflare Stream is not configured (STREAM_ACCOUNT_ID / STREAM_API_TOKEN / STREAM_CUSTOMER_CODE)",
        )


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {STREAM_API_TOKEN}",
        "Content-Type": "application/json",
    }


def hls_url(uid: str) -> str:
    code = STREAM_CUSTOMER_CODE.removeprefix("customer-")
    return f"https://customer-{code}.cloudflarestream.com/{uid}/manifest/video.m3u8"


def thumbnail_url(uid: str) -> str:
    code = STREAM_CUSTOMER_CODE.removeprefix("customer-")
    return f"https://customer-{code}.cloudflarestream.com/{uid}/thumbnails/thumbnail.jpg"


def iframe_url(uid: str) -> str:
    code = STREAM_CUSTOMER_CODE.removeprefix("customer-")
    return f"https://customer-{code}.cloudflarestream.com/{uid}/iframe"


def create_direct_upload(*, max_duration_seconds: int | None = None) -> dict[str, Any]:
    """Mint a one-time browser upload URL (multipart POST, files ≤200MB)."""
    require_stream()
    duration = max(1, min(3600, max_duration_seconds or STREAM_MAX_DURATION_SECONDS))
    url = f"{_API}/accounts/{STREAM_ACCOUNT_ID}/stream/direct_upload"
    payload = {
        "maxDurationSeconds": duration,
        "expiry": _expiry_iso(STREAM_UPLOAD_EXPIRY_SECONDS),
        "requireSignedURLs": False,
        "meta": {"app": "junction.monster", "kind": "short"},
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.post(url, headers=_headers(), json=payload)
    data = response.json() if response.content else {}
    if response.status_code >= 400 or not data.get("success"):
        detail = data.get("errors") or data.get("messages") or response.text
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Stream direct_upload failed: {detail}",
        )
    result = data.get("result") or {}
    uid = str(result.get("uid") or "").strip()
    upload_url = str(result.get("uploadURL") or "").strip()
    if not uid or not upload_url:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Stream direct_upload missing uid/uploadURL")
    return {
        "uid": uid,
        "upload_url": upload_url,
        "hls_url": hls_url(uid),
        "thumbnail_url": thumbnail_url(uid),
        "max_duration_seconds": duration,
        "expires_in": STREAM_UPLOAD_EXPIRY_SECONDS,
    }


def get_video(uid: str) -> dict[str, Any]:
    require_stream()
    url = f"{_API}/accounts/{STREAM_ACCOUNT_ID}/stream/{uid}"
    with httpx.Client(timeout=20.0) as client:
        response = client.get(url, headers=_headers())
    data = response.json() if response.content else {}
    if response.status_code >= 400 or not data.get("success"):
        detail = data.get("errors") or response.text
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Stream get failed: {detail}")
    return data.get("result") or {}


def ready_to_stream(uid: str) -> bool:
    try:
        info = get_video(uid)
    except Exception:
        return False
    if info.get("readyToStream") is True:
        return True
    state = str((info.get("status") or {}).get("state") or "").lower()
    return state == "ready"


def delete_video(uid: str | None) -> None:
    key = (uid or "").strip()
    if not key or not stream_configured():
        return
    url = f"{_API}/accounts/{STREAM_ACCOUNT_ID}/stream/{key}"
    try:
        with httpx.Client(timeout=20.0) as client:
            client.delete(url, headers=_headers())
    except Exception:
        logger.exception("Failed to delete Stream video %s", key)


def wait_until_ready_for_post(post_id: str, uid: str, *, attempts: int = 40, delay_sec: float = 3.0) -> str:
    """Background: poll Stream until ABR packager is ready, then mark the short ready."""
    from datetime import datetime, timezone

    from bson import ObjectId

    from .database import monster_posts

    if not ObjectId.is_valid(post_id) or not uid:
        return f"{post_id}: skip"
    for _ in range(max(1, attempts)):
        if ready_to_stream(uid):
            monster_posts.update_one(
                {"_id": ObjectId(post_id)},
                {
                    "$set": {
                        "status": "ready",
                        "stream_ready_at": datetime.now(timezone.utc),
                    },
                    "$unset": {"playback_error": ""},
                },
            )
            return f"{post_id}: stream ready"
        time.sleep(delay_sec)
    monster_posts.update_one(
        {"_id": ObjectId(post_id)},
        {
            "$set": {
                "status": "failed",
                "playback_error": "Stream encoding timed out",
                "playback_failed_at": datetime.now(timezone.utc),
            }
        },
    )
    return f"{post_id}: stream timeout"


def _expiry_iso(seconds: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(seconds=max(60, seconds))).strftime("%Y-%m-%dT%H:%M:%SZ")
