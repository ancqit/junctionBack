#!/usr/bin/env python3
"""Backfill lean H.264 CDN playback for existing shorts (R2 + GridFS masters).

Requires local ffmpeg on PATH and MONGODB_URL / MONGODB_DATABASE (or .env).
When R2 is configured, playback lands as `playback_key` on the CDN bucket.

  python scripts/backfill_short_playback.py --dry-run
  python scripts/backfill_short_playback.py --limit 20
  python scripts/backfill_short_playback.py --post-id 64f...
  python scripts/backfill_short_playback.py --force

Targets match app/short_playback.py: ~720×1280, ~2.8 Mbps, +faststart.
Feed players prefer playback_key / playback_video_id once set.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient

# Allow `python scripts/...` to import app.*
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import short_playback  # noqa: E402


def _env_db():
    load_dotenv()
    url = (os.getenv("MONGODB_URL") or "").strip()
    name = (os.getenv("MONGODB_DATABASE") or "junction").strip()
    if not url or "invalid" in url:
        raise SystemExit("Set MONGODB_URL (and optionally MONGODB_DATABASE) before running.")
    client = MongoClient(url, serverSelectionTimeoutMS=15_000)
    client.admin.command("ping")
    return client, client[name]["monster_posts"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="List work without writing")
    parser.add_argument("--limit", type=int, default=0, help="Max posts to process (0 = all)")
    parser.add_argument("--post-id", type=str, default="", help="Only this post ObjectId")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-encode even if playback_key / playback_video_id already exists",
    )
    args = parser.parse_args()

    if not short_playback.ffmpeg_available():
        raise SystemExit("ffmpeg not found on PATH — install it, then re-run.")

    client, posts = _env_db()
    try:
        query: dict = {
            "$or": [
                {"video_key": {"$exists": True, "$nin": [None, ""]}},
                {"video_id": {"$exists": True, "$nin": [None, ""]}},
            ]
        }
        if args.post_id.strip():
            query = {"_id": ObjectId(args.post_id.strip())}
        elif not args.force:
            query = {
                "$and": [
                    query,
                    {
                        "$or": [
                            {"playback_key": {"$exists": False}},
                            {"playback_key": None},
                            {"playback_key": ""},
                        ]
                    },
                    {
                        "$or": [
                            {"playback_video_id": {"$exists": False}},
                            {"playback_video_id": None},
                            {"playback_video_id": ""},
                        ]
                    },
                ]
            }

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
            post_id = str(doc["_id"])
            if args.dry_run:
                master = str(doc.get("video_key") or doc.get("video_id") or "")
                print(f"{post_id}: would encode master {master}")
                ok += 1
                continue
            try:
                line = short_playback.ensure_playback_for_post(post_id, force=args.force)
                print(line)
                if ": skip" not in line and ": fail" not in line:
                    ok += 1
            except Exception as exc:
                print(f"{post_id}: fail ({exc})")
        print(f"Done. {ok}/{len(docs)} handled.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
