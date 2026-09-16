#!/usr/bin/env python3
"""One-shot: re-encode existing short masters into a lean H.264 playback rendition.

Requires local ffmpeg on PATH. Uses MONGODB_URL / MONGODB_DATABASE from the
environment (or a local .env via python-dotenv).

  python scripts/backfill_short_playback.py --dry-run
  python scripts/backfill_short_playback.py --limit 20
  python scripts/backfill_short_playback.py --post-id 64f...
  python scripts/backfill_short_playback.py --promote   # replace master, drop old blob

Playback target matches jMonster lean uploads: ~720p, ~1.8 Mbps, +faststart.
Feed/post players use playback_video_id once set; download still uses the master
unless --promote is passed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId
from dotenv import load_dotenv
from gridfs import GridFS
from pymongo import MongoClient

# Lean playback — keep in sync with jMonster short-compress targets.
MAX_HEIGHT = 1280
VIDEO_BITRATE = "1800k"
AUDIO_BITRATE = "96k"
# Skip when master is already tiny (likely already lean).
SKIP_UNDER_BYTES = 3 * 1024 * 1024


def _env_db():
    load_dotenv()
    url = (os.getenv("MONGODB_URL") or "").strip()
    name = (os.getenv("MONGODB_DATABASE") or "junction").strip()
    if not url or "invalid" in url:
        raise SystemExit("Set MONGODB_URL (and optionally MONGODB_DATABASE) before running.")
    client = MongoClient(url, serverSelectionTimeoutMS=15_000)
    client.admin.command("ping")
    db = client[name]
    return client, db, GridFS(db, collection="monster_short_videos"), db["monster_posts"]


def _ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise SystemExit("ffmpeg not found on PATH — install it, then re-run.")
    return path


def _encode_lean(ffmpeg: str, src: Path, dest: Path) -> None:
    # Height ≤ 1280 (≈720×1280 vertical), even dims; H.264 + AAC; moov at front.
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
        "main",
        "-pix_fmt",
        "yuv420p",
        "-b:v",
        VIDEO_BITRATE,
        "-maxrate",
        "2000k",
        "-bufsize",
        "3600k",
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


def _process_one(
    *,
    posts,
    fs: GridFS,
    ffmpeg: str,
    doc: dict,
    dry_run: bool,
    promote: bool,
    force: bool,
) -> str:
    post_id = str(doc["_id"])
    video_id = str(doc.get("video_id") or "").strip()
    existing_playback = str(doc.get("playback_video_id") or "").strip()
    if not video_id or not ObjectId.is_valid(video_id):
        return f"{post_id}: skip (no video_id)"
    if existing_playback and not force:
        return f"{post_id}: skip (already has playback_video_id)"

    try:
        grid_out = fs.get(ObjectId(video_id))
    except Exception as exc:
        return f"{post_id}: skip (master missing: {exc})"

    master_size = int(getattr(grid_out, "length", 0) or 0)
    content_type = (grid_out.content_type or "video/mp4").split(";")[0].strip().lower()
    if not force and master_size and master_size <= SKIP_UNDER_BYTES and content_type == "video/mp4":
        return f"{post_id}: skip (master already ≤{SKIP_UNDER_BYTES // (1024 * 1024)}MB mp4)"

    if dry_run:
        return f"{post_id}: would encode master {master_size} bytes ({content_type})"

    suffix = ".webm" if "webm" in content_type else ".mov" if "quicktime" in content_type else ".mp4"
    with tempfile.TemporaryDirectory(prefix="short-play-") as tmp:
        src = Path(tmp) / f"master{suffix}"
        dest = Path(tmp) / "playback.mp4"
        with src.open("wb") as fh:
            fh.write(grid_out.read())
        _encode_lean(ffmpeg, src, dest)
        lean_bytes = dest.read_bytes()
        if not lean_bytes:
            return f"{post_id}: fail (empty encode)"

        new_id = fs.put(
            lean_bytes,
            content_type="video/mp4",
            filename=f"playback-{post_id}.mp4",
            metadata={
                "kind": "monster_short_playback",
                "source_video_id": video_id,
                "encoded_at": datetime.now(timezone.utc).isoformat(),
                "bytes": len(lean_bytes),
            },
        )
        update: dict = {
            "playback_video_id": str(new_id),
            "playback_bytes": len(lean_bytes),
            "playback_encoded_at": datetime.now(timezone.utc),
        }

        if promote:
            # Point video_id at lean file; drop previous master (+ old playback if forced).
            old_ids = {video_id}
            if existing_playback:
                old_ids.add(existing_playback)
            update["video_id"] = str(new_id)
            update["playback_video_id"] = str(new_id)
            posts.update_one({"_id": doc["_id"]}, {"$set": update})
            for oid in old_ids:
                if oid != str(new_id) and ObjectId.is_valid(oid):
                    try:
                        fs.delete(ObjectId(oid))
                    except Exception:
                        pass
            return (
                f"{post_id}: promoted playback {len(lean_bytes)} bytes "
                f"(was {master_size}; deleted master)"
            )

        if existing_playback and ObjectId.is_valid(existing_playback):
            try:
                fs.delete(ObjectId(existing_playback))
            except Exception:
                pass
        posts.update_one({"_id": doc["_id"]}, {"$set": update})
        return f"{post_id}: playback {len(lean_bytes)} bytes (master {master_size} kept)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="List work without writing")
    parser.add_argument("--limit", type=int, default=0, help="Max posts to process (0 = all)")
    parser.add_argument("--post-id", type=str, default="", help="Only this post ObjectId")
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Replace master with lean file and delete the old master (saves Atlas space)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-encode even if playback_video_id exists or master is already small",
    )
    args = parser.parse_args()

    ffmpeg = _ffmpeg_bin()
    client, _db, fs, posts = _env_db()
    try:
        query: dict = {"video_id": {"$exists": True, "$ne": ""}}
        if args.post_id.strip():
            query["_id"] = ObjectId(args.post_id.strip())
        elif not args.force:
            query["$or"] = [
                {"playback_video_id": {"$exists": False}},
                {"playback_video_id": None},
                {"playback_video_id": ""},
            ]

        cursor = posts.find(query).sort("created_at", -1)
        if args.limit and args.limit > 0:
            cursor = cursor.limit(args.limit)

        docs = list(cursor)
        if not docs:
            print("No shorts need a playback backfill.")
            return 0

        print(f"Processing {len(docs)} short(s)…")
        ok = 0
        for doc in docs:
            try:
                line = _process_one(
                    posts=posts,
                    fs=fs,
                    ffmpeg=ffmpeg,
                    doc=doc,
                    dry_run=args.dry_run,
                    promote=args.promote,
                    force=args.force,
                )
                print(line)
                if not line.startswith(str(doc["_id"]) + ": skip") and not line.startswith(
                    str(doc["_id"]) + ": fail"
                ):
                    ok += 1
                elif "would encode" in line:
                    ok += 1
            except subprocess.CalledProcessError as exc:
                print(f"{doc['_id']}: fail (ffmpeg exit {exc.returncode})")
            except Exception as exc:
                print(f"{doc['_id']}: fail ({exc})")
        print(f"Done. {ok}/{len(docs)} handled.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
