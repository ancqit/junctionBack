"""CDN-ready H.264 playback for shorts (Instagram / YouTube Shorts style).

Masters stay on R2/GridFS for download. Feed players prefer `playback_key`
(H.264 + AAC + faststart ~720×1280) once this module finishes encoding.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId
from gridfs import GridFS

from . import r2_media
from .database import database, monster_posts

logger = logging.getLogger(__name__)

# Phone-HD vertical — sharp on modern screens, still starts fast on CDN.
MAX_HEIGHT = 1280
VIDEO_BITRATE = "2800k"
VIDEO_MAXRATE = "3500k"
VIDEO_BUFSIZE = "5000k"
AUDIO_BITRATE = "128k"
# Already tiny mp4 masters are fine to re-wrap for faststart; skip only empties.
MIN_MASTER_BYTES = 8_000

short_video_fs = GridFS(database, collection="monster_short_videos")


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg"))


def _ffmpeg_bin() -> str | None:
    return shutil.which("ffmpeg")


def encode_lean_file(src: Path, dest: Path) -> None:
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-vf",
        f"scale=-2:'min({MAX_HEIGHT},ih)'",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-b:v",
        VIDEO_BITRATE,
        "-maxrate",
        VIDEO_MAXRATE,
        "-bufsize",
        VIDEO_BUFSIZE,
        "-c:a",
        "aac",
        "-b:a",
        AUDIO_BITRATE,
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-t",
        "30",
        str(dest),
    ]
    subprocess.run(cmd, check=True)


def ensure_playback_for_post(post_id: str, *, force: bool = False) -> str:
    """Encode a lean H.264 playback object and attach `playback_key` / GridFS id.

    Safe to call from a FastAPI BackgroundTask. Returns a short status string.
    """
    if not ObjectId.is_valid(post_id):
        return f"{post_id}: skip (bad id)"
    doc = monster_posts.find_one({"_id": ObjectId(post_id)})
    if not doc:
        return f"{post_id}: skip (missing)"

    existing_key = str(doc.get("playback_key") or "").strip()
    existing_grid = str(doc.get("playback_video_id") or "").strip()
    if (existing_key or existing_grid) and not force:
        return f"{post_id}: skip (already has playback)"

    if not ffmpeg_available():
        return f"{post_id}: skip (ffmpeg missing)"

    video_key = str(doc.get("video_key") or "").strip()
    video_id = str(doc.get("video_id") or "").strip()

    try:
        if video_key and r2_media.r2_configured():
            return _encode_from_r2(post_id, doc, video_key, force=force)
        if video_id and ObjectId.is_valid(video_id):
            return _encode_from_gridfs(post_id, doc, video_id, force=force)
        return f"{post_id}: skip (no master)"
    except Exception as exc:
        logger.exception("playback encode failed for %s", post_id)
        monster_posts.update_one(
            {"_id": ObjectId(post_id)},
            {
                "$set": {
                    "playback_error": str(exc)[:240],
                    "playback_failed_at": datetime.now(timezone.utc),
                }
            },
        )
        return f"{post_id}: fail ({exc})"


def _suffix_for_type(content_type: str, key_or_name: str) -> str:
    lower = (content_type or "").lower()
    name = (key_or_name or "").lower()
    if "webm" in lower or name.endswith(".webm"):
        return ".webm"
    if "quicktime" in lower or name.endswith(".mov"):
        return ".mov"
    return ".mp4"


def _encode_from_r2(post_id: str, doc: dict, video_key: str, *, force: bool) -> str:
    master = r2_media.get_bytes(video_key)
    if len(master) < MIN_MASTER_BYTES:
        return f"{post_id}: skip (master too small)"

    with tempfile.TemporaryDirectory(prefix="short-play-") as tmp:
        src = Path(tmp) / f"master{_suffix_for_type('', video_key)}"
        dest = Path(tmp) / "playback.mp4"
        src.write_bytes(master)
        encode_lean_file(src, dest)
        lean = dest.read_bytes()
        if not lean:
            return f"{post_id}: fail (empty encode)"

        playback_key = r2_media.new_playback_key()
        r2_media.put_bytes(
            playback_key,
            lean,
            content_type="video/mp4",
        )
        old_key = str(doc.get("playback_key") or "").strip()
        monster_posts.update_one(
            {"_id": ObjectId(post_id)},
            {
                "$set": {
                    "playback_key": playback_key,
                    "playback_bytes": len(lean),
                    "playback_encoded_at": datetime.now(timezone.utc),
                    "playback_content_type": "video/mp4",
                },
                "$unset": {"playback_error": "", "playback_failed_at": ""},
            },
        )
        if force and old_key and old_key != playback_key:
            r2_media.delete_object(old_key)
        return f"{post_id}: playback_key {len(lean)} bytes (master {len(master)})"


def _encode_from_gridfs(post_id: str, doc: dict, video_id: str, *, force: bool) -> str:
    try:
        grid_out = short_video_fs.get(ObjectId(video_id))
    except Exception as exc:
        return f"{post_id}: skip (master missing: {exc})"

    master_size = int(getattr(grid_out, "length", 0) or 0)
    content_type = (grid_out.content_type or "video/mp4").split(";")[0].strip().lower()
    if master_size < MIN_MASTER_BYTES:
        return f"{post_id}: skip (master too small)"

    with tempfile.TemporaryDirectory(prefix="short-play-") as tmp:
        src = Path(tmp) / f"master{_suffix_for_type(content_type, '')}"
        dest = Path(tmp) / "playback.mp4"
        src.write_bytes(grid_out.read())
        encode_lean_file(src, dest)
        lean = dest.read_bytes()
        if not lean:
            return f"{post_id}: fail (empty encode)"

        # Prefer R2 when configured so feeds leave Render.
        if r2_media.r2_configured():
            playback_key = r2_media.new_playback_key()
            r2_media.put_bytes(playback_key, lean, content_type="video/mp4")
            monster_posts.update_one(
                {"_id": ObjectId(post_id)},
                {
                    "$set": {
                        "playback_key": playback_key,
                        "playback_bytes": len(lean),
                        "playback_encoded_at": datetime.now(timezone.utc),
                        "playback_content_type": "video/mp4",
                    },
                    "$unset": {"playback_error": "", "playback_failed_at": ""},
                },
            )
            return f"{post_id}: playback_key {len(lean)} bytes from GridFS master"

        new_id = short_video_fs.put(
            lean,
            content_type="video/mp4",
            filename=f"playback-{post_id}.mp4",
            metadata={
                "kind": "monster_short_playback",
                "source_video_id": video_id,
                "encoded_at": datetime.now(timezone.utc).isoformat(),
                "bytes": len(lean),
            },
        )
        existing = str(doc.get("playback_video_id") or "").strip()
        monster_posts.update_one(
            {"_id": ObjectId(post_id)},
            {
                "$set": {
                    "playback_video_id": str(new_id),
                    "playback_bytes": len(lean),
                    "playback_encoded_at": datetime.now(timezone.utc),
                },
                "$unset": {"playback_error": "", "playback_failed_at": ""},
            },
        )
        if force and existing and ObjectId.is_valid(existing) and existing != str(new_id):
            try:
                short_video_fs.delete(ObjectId(existing))
            except Exception:
                pass
        return f"{post_id}: playback_video_id {len(lean)} bytes"
