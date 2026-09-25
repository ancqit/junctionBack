"""
Waste archive crawler helper for jEarth.

- Seeds a household archive into MongoDB
- Fetches public pages (Wikipedia + curated waste guides)
- Classifies stream + disposal hints with simple keyword rules
- Upserts into `waste_archive` for Google-like search on junction.earth

Run via:
  python -m scripts.waste_archive_crawler
  POST /internal/jobs/waste-archive-crawl  (X-Cron-Secret)
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

import httpx

from .database import waste_archive

USER_AGENT = "JunctionEarthWasteBot/1.0 (+https://junction.earth; waste-archive helper)"
STREAMS = (
    "wet",
    "dry",
    "sanitary",
    "ewaste",
    "hazardous",
    "reject",
    "construction",
)

# Public pages worth refreshing periodically (allowlist — not open crawl).
CURATED_SOURCES: list[dict[str, str]] = [
    {
        "url": "https://en.wikipedia.org/api/rest_v1/page/summary/Waste_management",
        "kind": "wikipedia",
        "id": "wiki-waste-management",
        "name_en": "Waste management",
        "name_hi": "कचरा प्रबंधन",
    },
    {
        "url": "https://en.wikipedia.org/api/rest_v1/page/summary/Recycling",
        "kind": "wikipedia",
        "id": "wiki-recycling",
        "name_en": "Recycling",
        "name_hi": "रीसाइक्लिंग",
    },
    {
        "url": "https://en.wikipedia.org/api/rest_v1/page/summary/Compost",
        "kind": "wikipedia",
        "id": "wiki-compost",
        "name_en": "Compost",
        "name_hi": "खाद",
    },
    {
        "url": "https://en.wikipedia.org/api/rest_v1/page/summary/Electronic_waste",
        "kind": "wikipedia",
        "id": "wiki-ewaste",
        "name_en": "Electronic waste",
        "name_hi": "ई-वेस्ट",
    },
    {
        "url": "https://en.wikipedia.org/api/rest_v1/page/summary/Hazardous_waste",
        "kind": "wikipedia",
        "id": "wiki-hazardous",
        "name_en": "Hazardous waste",
        "name_hi": "खतरनाक कचरा",
    },
    {
        "url": "https://en.wikipedia.org/api/rest_v1/page/summary/Biodegradable_waste",
        "kind": "wikipedia",
        "id": "wiki-biodegradable",
        "name_en": "Biodegradable waste",
        "name_hi": "जैविक कचरा",
    },
]

# Seed items (same idea as jEarth static archive) — search keywords for web enrichment.
SEED_ENTRIES: list[dict[str, Any]] = [
    {
        "id": "peel",
        "stream": "wet",
        "name_en": "Fruit or vegetable peel",
        "name_hi": "फल या सब्ज़ी का छिलका",
        "aliases_en": ["peel", "mango", "banana", "onion", "potato", "vegetable scrap", "fruit"],
        "aliases_hi": ["छिलका", "आम", "केला", "प्याज़", "आलू", "सब्ज़ी", "फल"],
        "dispose_en": [
            "Home or community compost.",
            "Municipal wet-waste truck.",
            "Never the dry bag or the drain.",
        ],
        "dispose_hi": [
            "घर या कॉलोनी की खाद।",
            "नगर निगम की गीले कचरे की गाड़ी।",
            "सूखे बैग या नाले में कभी नहीं।",
        ],
        "search_query": "fruit vegetable peel compost wet waste disposal India",
    },
    {
        "id": "pet-bottle",
        "stream": "dry",
        "name_en": "Plastic water or soft-drink bottle",
        "name_hi": "पानी या ठंडे पेय की प्लास्टिक बोतल",
        "aliases_en": ["bottle", "pet", "plastic bottle", "water bottle"],
        "aliases_hi": ["बोतल", "प्लास्टिक बोतल", "पानी की बोतल"],
        "dispose_en": [
            "Rinse, crush lightly, dry bag / scrap shop / MRF.",
            "Follow local cap rules — many want the cap on.",
        ],
        "dispose_hi": [
            "धोकर हल्का दबाएँ, सूखे बैग / कबाड़ी / MRF।",
            "ढक्कन के स्थानीय नियम देखें।",
        ],
        "search_query": "PET plastic bottle recycle disposal India",
    },
    {
        "id": "sanitary-pad",
        "stream": "sanitary",
        "name_en": "Used sanitary pad or tampon",
        "name_hi": "इस्तेमाल सैनिटरी पैड या टैम्पोन",
        "aliases_en": ["pad", "sanitary", "tampon", "napkin"],
        "aliases_hi": ["पैड", "सैनिटरी", "नैपकिन"],
        "dispose_en": [
            "Wrap in newspaper or marked bag → sanitary / yellow stream.",
            "Never compost, never dry recycling, never open burning.",
        ],
        "dispose_hi": [
            "अखबार या निशान वाले बैग में लपेटें → सैनिटरी / पीला।",
            "खाद, सूखा रीसायकल या खुली आग में कभी नहीं।",
        ],
        "search_query": "sanitary pad disposal India wrap yellow bag",
    },
    {
        "id": "phone-charger",
        "stream": "ewaste",
        "name_en": "Dead phone charger or cable",
        "name_hi": "खराब फ़ोन चार्जर या केबल",
        "aliases_en": ["charger", "cable", "wire", "usb", "earphone"],
        "aliases_hi": ["चार्जर", "केबल", "तार", "ईयरफ़ोन"],
        "dispose_en": [
            "E-waste collection / authorised recycler.",
            "Do not mix with dry plastic; do not burn insulation.",
        ],
        "dispose_hi": [
            "ई-वेस्ट संग्रह / अधिकृत रीसाइक्लर।",
            "सूखे प्लास्टिक में न मिलाएँ; इंसुलेशन न जलाएँ।",
        ],
        "search_query": "e-waste charger cable disposal India authorised recycler",
    },
    {
        "id": "battery",
        "stream": "hazardous",
        "name_en": "Dead battery (AA, button, lithium)",
        "name_hi": "खत्म बैटरी (AA, बटन, लिथियम)",
        "aliases_en": ["battery", "aa", "lithium", "cell"],
        "aliases_hi": ["बैटरी", "सेल"],
        "dispose_en": [
            "Battery collection / hazardous drop-off — not mixed dry.",
            "Button and lithium are fire risks in trucks.",
        ],
        "dispose_hi": [
            "बैटरी संग्रह / खतरनाक ड्रॉप — मिश्रित सूखे में नहीं।",
            "बटन और लिथियम ट्रक में आग का खतरा।",
        ],
        "search_query": "used battery disposal India hazardous collection",
    },
    {
        "id": "snack-pouch",
        "stream": "reject",
        "name_en": "Metallised snack pouch",
        "name_hi": "चमकदार नमकीन पाउच",
        "aliases_en": ["pouch", "chips packet", "laminate", "wrapper"],
        "aliases_hi": ["पाउच", "चिप्स पैकेट", "रैपर", "पैकेट"],
        "dispose_en": [
            "Residual / reject — mixed layers, rarely recycled from homes.",
            "Refuse next time at the shop if a refill or simpler pack exists.",
        ],
        "dispose_hi": [
            "रिजेक्ट / अवशेष — कई परतें, घर से कम रीसायकल।",
            "अगली बार दुकान पर इनकार करें अगर सरल पैक हो।",
        ],
        "search_query": "multilayer plastic pouch waste residual India",
    },
    {
        "id": "medicine",
        "stream": "hazardous",
        "name_en": "Expired medicines",
        "name_hi": "एक्सपायर्ड दवाई",
        "aliases_en": ["medicine", "tablet", "antibiotic", "pharmacy"],
        "aliases_hi": ["दवाई", "गोली", "मेडिसिन"],
        "dispose_en": [
            "Pharmacy take-back or domestic hazardous collection.",
            "Do not flush. Do not put in wet compost.",
        ],
        "dispose_hi": [
            "फ़ार्मेसी टेक-बैक या घरेलू खतरनाक संग्रह।",
            "फ्लश न करें। गीली खाद में न डालें।",
        ],
        "search_query": "expired medicine disposal India pharmacy take back",
    },
    {
        "id": "cnd-rubble",
        "stream": "construction",
        "name_en": "Brick, concrete, or renovation rubble",
        "name_hi": "ईंट, कंक्रीट या मरम्मत का मलबा",
        "aliases_en": ["rubble", "brick", "concrete", "debris", "construction"],
        "aliases_hi": ["मलबा", "ईंट", "कंक्रीट", "निर्माण"],
        "dispose_en": [
            "C&D stream — authorised debris site, not household wet/dry.",
            "Separate clean soil, metal, and wood when possible.",
        ],
        "dispose_hi": [
            "निर्माण मलबा — अधिकृत स्थल, घरेलू गीला/सूखा नहीं।",
            "जहाँ हो साफ़ मिट्टी, धातु, लकड़ी अलग करें।",
        ],
        "search_query": "construction demolition waste disposal India C&D",
    },
]

_STREAM_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("sanitary", ("sanitary", "pad", "diaper", "tampon", "nappy", "menstrual")),
    ("ewaste", ("e-waste", "ewaste", "electronic", "charger", "laptop", "phone", "battery lithium")),
    ("hazardous", ("hazardous", "toxic", "chemical", "paint", "solvent", "medicine", "pharmaceutical", "battery")),
    ("construction", ("construction", "demolition", "c&d", "rubble", "concrete", "brick")),
    ("wet", ("compost", "biodegradable", "organic", "wet waste", "food waste", "kitchen")),
    ("dry", ("recycl", "dry waste", "pet bottle", "paper", "cardboard", "glass", "metal")),
    ("reject", ("residual", "reject", "landfill", "multilayer", "laminate", "thermocol")),
]


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if text:
            self.parts.append(text)


def ensure_waste_archive_indexes() -> None:
    waste_archive.create_index("id", unique=True)
    waste_archive.create_index(
        [
            ("name_en", "text"),
            ("name_hi", "text"),
            ("aliases_en", "text"),
            ("aliases_hi", "text"),
            ("snippet_en", "text"),
            ("snippet_hi", "text"),
        ],
        name="waste_archive_text",
        default_language="none",
    )


def classify_stream(text: str, fallback: str = "reject") -> str:
    lower = (text or "").lower()
    for stream, needles in _STREAM_RULES:
        if any(n in lower for n in needles):
            return stream
    return fallback if fallback in STREAMS else "reject"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(value: str) -> str:
    raw = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return raw[:80] or hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def upsert_entry(doc: dict[str, Any]) -> None:
    entry_id = str(doc.get("id") or "").strip()
    if not entry_id:
        raise ValueError("waste archive entry requires id")
    payload = {**doc, "id": entry_id, "updated_at": _now()}
    payload.setdefault("created_at", _now())
    waste_archive.update_one(
        {"id": entry_id},
        {
            "$set": {k: v for k, v in payload.items() if k != "created_at"},
            "$setOnInsert": {"created_at": payload["created_at"]},
        },
        upsert=True,
    )


def seed_archive(force: bool = False) -> dict[str, int]:
    """Load curated household seeds into Mongo."""
    inserted = 0
    updated = 0
    for seed in SEED_ENTRIES:
        existing = waste_archive.find_one({"id": seed["id"]}, {"_id": 1})
        if existing and not force:
            continue
        doc = {
            **seed,
            "snippet_en": " ".join(seed.get("dispose_en") or [])[:400],
            "snippet_hi": " ".join(seed.get("dispose_hi") or [])[:400],
            "sources": [],
            "origin": "seed",
        }
        upsert_entry(doc)
        if existing:
            updated += 1
        else:
            inserted += 1
    return {"seeded": inserted, "updated": updated, "total": waste_archive.count_documents({})}


def _http_get_json(url: str, timeout: float = 20.0) -> dict[str, Any] | None:
    try:
        with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
            response = client.get(url)
            if response.status_code >= 400:
                return None
            return response.json()
    except Exception:
        return None


def _http_get_text(url: str, timeout: float = 20.0) -> str | None:
    try:
        with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
            response = client.get(url)
            if response.status_code >= 400:
                return None
            return response.text
    except Exception:
        return None


def fetch_wikipedia_summary(api_url: str) -> dict[str, Any] | None:
    data = _http_get_json(api_url)
    if not data or data.get("type") == "disambiguation":
        return None
    extract = (data.get("extract") or "").strip()
    title = (data.get("title") or "").strip()
    page_url = (
        (data.get("content_urls") or {}).get("desktop", {}) or {}
    ).get("page") or api_url
    if not title or not extract:
        return None
    return {
        "title": title,
        "extract": extract,
        "url": page_url,
        "thumbnail": ((data.get("thumbnail") or {}) or {}).get("source"),
    }


def fetch_duckduckgo_instant(query: str) -> dict[str, Any] | None:
    """Lightweight public instant-answer API (no key). Used to enrich seeds."""
    url = f"https://api.duckduckgo.com/?q={quote(query)}&format=json&no_html=1&skip_disambig=1"
    data = _http_get_json(url)
    if not data:
        return None
    abstract = (data.get("AbstractText") or "").strip()
    heading = (data.get("Heading") or "").strip()
    source_url = (data.get("AbstractURL") or "").strip()
    if not abstract:
        return None
    return {"title": heading or query, "extract": abstract, "url": source_url or url}


def html_to_text(html: str, limit: int = 1200) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)[:limit]
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()[:limit]


def crawl_curated_sources() -> dict[str, int]:
    ok = 0
    fail = 0
    for source in CURATED_SOURCES:
        summary = fetch_wikipedia_summary(source["url"])
        if not summary:
            fail += 1
            continue
        stream = classify_stream(f"{summary['title']} {summary['extract']}")
        upsert_entry(
            {
                "id": source["id"],
                "stream": stream,
                "name_en": source.get("name_en") or summary["title"],
                "name_hi": source.get("name_hi") or summary["title"],
                "aliases_en": [summary["title"].lower()],
                "aliases_hi": [],
                "dispose_en": [
                    summary["extract"][:360],
                    f"Source: {summary['url']}",
                ],
                "dispose_hi": [
                    summary["extract"][:360],
                    f"स्रोत: {summary['url']}",
                ],
                "snippet_en": summary["extract"][:400],
                "snippet_hi": summary["extract"][:400],
                "sources": [{"url": summary["url"], "title": summary["title"]}],
                "origin": "crawl",
            }
        )
        ok += 1
    return {"curated_ok": ok, "curated_fail": fail}


def enrich_seeds_from_web(limit: int = 20) -> dict[str, int]:
    """For each seed, pull a DuckDuckGo abstract and merge as a source snippet."""
    ok = 0
    fail = 0
    for seed in SEED_ENTRIES[:limit]:
        hit = fetch_duckduckgo_instant(seed.get("search_query") or seed["name_en"])
        if not hit:
            fail += 1
            continue
        existing = waste_archive.find_one({"id": seed["id"]}) or dict(seed)
        sources = list(existing.get("sources") or [])
        url = hit.get("url") or ""
        if url and not any(s.get("url") == url for s in sources):
            sources.append({"url": url, "title": hit.get("title") or seed["name_en"]})
        snippet = hit["extract"][:400]
        dispose_en = list(existing.get("dispose_en") or seed.get("dispose_en") or [])
        if snippet and snippet not in dispose_en:
            dispose_en = [*dispose_en[:3], snippet]
        upsert_entry(
            {
                **{k: existing.get(k, seed.get(k)) for k in (
                    "id",
                    "stream",
                    "name_en",
                    "name_hi",
                    "aliases_en",
                    "aliases_hi",
                    "dispose_hi",
                )},
                "dispose_en": dispose_en,
                "snippet_en": snippet or existing.get("snippet_en"),
                "snippet_hi": existing.get("snippet_hi") or seed.get("snippet_hi"),
                "sources": sources,
                "origin": existing.get("origin") or "seed",
                "web_enriched_at": _now(),
            }
        )
        ok += 1
    return {"enrich_ok": ok, "enrich_fail": fail}


def run_waste_archive_crawl(*, force_seed: bool = False, enrich: bool = True) -> dict[str, Any]:
    """Full helper pass: indexes → seed → curated crawl → optional web enrich."""
    ensure_waste_archive_indexes()
    seed_stats = seed_archive(force=force_seed)
    curated = crawl_curated_sources()
    enriched = enrich_seeds_from_web() if enrich else {"enrich_ok": 0, "enrich_fail": 0}
    return {
        "ran_at": _now().isoformat(),
        **seed_stats,
        **curated,
        **enriched,
        "archive_count": waste_archive.count_documents({}),
    }


def search_waste_archive(query: str, *, lang: str = "en", limit: int = 12) -> list[dict[str, Any]]:
    """Google-like ranked search over the Mongo archive."""
    q = (query or "").strip()
    if not q:
        return []
    limit = max(1, min(int(limit or 12), 30))
    lang = "hi" if lang == "hi" else "en"

    rows: list[dict[str, Any]] = []
    try:
        cursor = (
            waste_archive.find(
                {"$text": {"$search": q}},
                {"score": {"$meta": "textScore"}, "_id": 0},
            )
            .sort([("score", {"$meta": "textScore"})])
            .limit(limit)
        )
        rows = list(cursor)
    except Exception:
        rows = []

    if not rows:
        # Fallback: case-insensitive substring across name/aliases.
        pattern = re.compile(re.escape(q), re.IGNORECASE)
        cursor = waste_archive.find(
            {
                "$or": [
                    {"name_en": pattern},
                    {"name_hi": pattern},
                    {"aliases_en": pattern},
                    {"aliases_hi": pattern},
                    {"snippet_en": pattern},
                    {"id": pattern},
                ]
            },
            {"_id": 0},
        ).limit(limit)
        rows = list(cursor)

    results: list[dict[str, Any]] = []
    for row in rows:
        name = row.get("name_hi") if lang == "hi" else row.get("name_en")
        dispose = row.get("dispose_hi") if lang == "hi" else row.get("dispose_en")
        snippet = row.get("snippet_hi") if lang == "hi" else row.get("snippet_en")
        if not snippet and isinstance(dispose, list):
            snippet = " ".join(str(x) for x in dispose)[:280]
        results.append(
            {
                "id": row.get("id"),
                "title": name or row.get("name_en") or row.get("id"),
                "stream": row.get("stream") or "reject",
                "snippet": snippet or "",
                "dispose": dispose if isinstance(dispose, list) else [],
                "sources": row.get("sources") or [],
                "score": row.get("score"),
            }
        )
    return results
