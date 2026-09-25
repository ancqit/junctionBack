#!/usr/bin/env python3
"""CLI helper: crawl / seed the jEarth waste archive in MongoDB.

Usage (from junction-back root, with MONGODB_URL set):

  python -m scripts.waste_archive_crawler
  python -m scripts.waste_archive_crawler --force-seed --no-enrich
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python -m scripts.waste_archive_crawler` from repo root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def main() -> int:
    parser = argparse.ArgumentParser(description="Update the jEarth waste archive in MongoDB")
    parser.add_argument("--force-seed", action="store_true", help="Overwrite seed documents")
    parser.add_argument("--no-enrich", action="store_true", help="Skip DuckDuckGo enrich step")
    args = parser.parse_args()

    from app.waste_crawler import run_waste_archive_crawl

    result = run_waste_archive_crawl(force_seed=args.force_seed, enrich=not args.no_enrich)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
