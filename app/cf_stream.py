"""Cloudflare Stream — managed ABR video (closest to YouTube/Instagram delivery).

When configured, shorts upload via Direct Creator Upload and play from HLS.
Playback URLs come from the Stream API (`playback.hls`) — you do **not** need
STREAM_CUSTOMER_CODE unless you want URLs before the first ready poll.

R2 remains for audio/posters and as video fallback when Stream is unset.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

import httpx
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

STREAM_ACCOUNT_ID = (os.getenv("STREAM_ACCOUNT_ID") or os.getenv("R2_ACCOUNT_ID") or "").strip()
STREAM_API_TOKEN = (os.getenv("STREAM_API_TOKEN") or "").strip()
# Optional. Same for every video on the account (customer-<CODE>.cloudflarestream.com).
# If unset, we read HLS/thumbnail from the Stream API when the video is ready.
STREAM_CUSTOMER_CODE = (os.getenv("STREAM_CUSTOMER_CODE") or "").strip()
STREAM_MAX_DURATION_SECONDS = int(os.getenv("STREAM_MAX_DURATION_SECONDS") or "30")
STREAM_UPLOAD_EXPIRY_SECONDS = int(os.getenv("STREAM_UPLOAD_EXPIRY_SECONDS") or "600")

_API = "https://api.cloudflare.com/client/v4"
_CUSTOMER_RE = re.compile(r"customer-([a-zA-Z0-9]+)\.cloudflarestream\.com", re.I)

# Cached from API responses when STREAM_CUSTOMER_CODE is not set.
_discovered_customer_code: str | None = None


def stream_configured() -> bool:
    """Account id + API token are enough — customer code is optional."""
    return bool(STREAM_ACCOUNT_ID and STREAM_API_TOKEN)


def require_stream() -> None:
    if not stream_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cloudflare Stream is not configured (STREAM_ACCOUNT_ID / STREAM_API_TOKEN)",
        )


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {STREAM_API_TOKEN}",
        "Content-Type": "application/json",
    }


def _customer_code() -> str:
    global _discovered_customer_code
    if STREAM_CUSTOMER_CODE:
        return STREAM_CUSTOMER_CODE.removeprefix("customer-")
    return (_discovered_customer_code or "").removeprefix("customer-")


def _remember_customer_from_url(url: str) -> None:
    global _discovered_customer_code
    if _discovered_customer_code or STREAM_CUSTOMER_CODE:
        return
    match = _CUSTOMER_RE.search(url or "")
    if match:
        _discovered_customer_code = match.group(1)


def playback_urls_from_video(info: dict[str, Any], uid: str) -> tuple[str, str]:
    """Return (hls_url, thumbnail_url) from a Stream video details payload."""
    playback = info.get("playback") if isinstance(info.get("playback"), dict) else {}
    hls = str(playback.get("hls") or "").strip()
    thumb = ""
    # thumbnail can be a string or nested
    raw_thumb = info.get("thumbnail")
    if isinstance(raw_thumb, str):
        thumb = raw_thumb.strip()
    elif isinstance(raw_thumb, dict):
        thumb = str(raw_thumb.get("url") or "").strip()
    if hls:
        _remember_customer_from_url(hls)
    if thumb:
        _remember_customer_from_url(thumb)
    if not hls:
        hls = hls_url(uid)
    if not thumb:
        thumb = thumbnail_url(uid)
    return hls, thumb


def hls_url(uid: str) -> str:
    code = _customer_code()
    if not code or not uid:
        return ""
    return f"https://customer-{code}.cloudflarestream.com/{uid}/manifest/video.m3u8"


def thumbnail_url(uid: str) -> str:
    code = _customer_code()
    if not code or not uid:
        return ""
    return f"https://customer-{code}.cloudflarestream.com/{uid}/thumbnails/thumbnail.jpg"


def iframe_url(uid: str) -> str:
    code = _customer_code()
    if not code or not uid:
        return ""
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
    # HLS may be empty until STREAM_CUSTOMER_CODE is known or the video is ready.
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
    """Background: poll Stream until ABR packager is ready, then store HLS URLs + mark ready."""
    from datetime import datetime, timezone

    from bson import ObjectId

    from .database import monster_posts

    if not ObjectId.is_valid(post_id) or not uid:
        return f"{post_id}: skip"
    for _ in range(max(1, attempts)):
        try:
            info = get_video(uid)
        except Exception:
            time.sleep(delay_sec)
            continue
        ready = info.get("readyToStream") is True or str((info.get("status") or {}).get("state") or "").lower() == "ready"
        if ready:
            hls, thumb = playback_urls_from_video(info, uid)
            update: dict[str, Any] = {
                "status": "ready",
                "stream_ready_at": datetime.now(timezone.utc),
            }
            if hls:
                update["stream_hls_url"] = hls
            if thumb:
                update["stream_thumbnail_url"] = thumb
            monster_posts.update_one(
                {"_id": ObjectId(post_id)},
                {"$set": update, "$unset": {"playback_error": ""}},
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
