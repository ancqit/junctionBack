"""
Learn-on-miss for the jEarth waste archive.

When a search finds nothing, look the item up on Wikipedia (Hindi or English by
script), and if a page clearly names a waste item, store it in `waste_archive`
with bilingual disposal advice for its stream so the next visitor finds it.
Every miss is counted in `waste_search_misses`; the weekly crawl retries the
ones still unanswered.
"""

from __future__ import annotations

import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

import httpx

from .database import waste_archive, waste_search_misses
from .waste_crawler import USER_AGENT, upsert_entry

MIN_QUERY_CHARS = 3
LIVE_LOOKUP_COOLDOWN = timedelta(hours=24)
CRAWL_RETRY_AFTER = timedelta(days=6)
LIVE_LOOKUPS_PER_MINUTE = 20
LOOKUP_DEADLINE_S = 5.0
HTTP_TIMEOUT_S = 3.0

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")

# Matched as whole words against page titles (and, more cautiously, extracts).
# Order matters: the first stream with a hit wins.
STREAM_NEEDLES: list[tuple[str, tuple[str, ...]]] = [
    (
        "sanitary",
        (
            "sanitary",
            "diaper",
            "nappy",
            "tampon",
            "menstrual",
            "face mask",
            "bandage",
            "डायपर",
            "सैनिटरी",
        ),
    ),
    (
        "ewaste",
        (
            "phone",
            "smartphone",
            "mobile phone",
            "charger",
            "laptop",
            "computer",
            "tablet computer",
            "television",
            "monitor",
            "printer",
            "keyboard",
            "headphones",
            "earphones",
            "lamp",
            "light bulb",
            "bulb",
            "fluorescent",
            "compact fluorescent",
            "led lamp",
            "electronic",
            "e-waste",
            "router",
            "remote control",
            "फ़ोन",
            "कंप्यूटर",
            "बल्ब",
        ),
    ),
    (
        "hazardous",
        (
            "battery",
            "batteries",
            "medicine",
            "medication",
            "pharmaceutical",
            "paint",
            "pesticide",
            "insecticide",
            "mercury",
            "thermometer",
            "syringe",
            "hypodermic needle",
            "solvent",
            "bleach",
            "aerosol",
            "बैटरी",
            "दवा",
        ),
    ),
    (
        "construction",
        (
            "rubble",
            "brick",
            "concrete",
            "cement",
            "tile",
            "demolition",
            "construction waste",
            "मलबा",
            "ईंट",
        ),
    ),
    (
        "wet",
        (
            "food",
            "fruit",
            "vegetable",
            "peel",
            "coconut",
            "banana",
            "eggshell",
            "egg",
            "tea",
            "coffee",
            "leaf",
            "leaves",
            "flower",
            "garden waste",
            "compost",
            "food waste",
            "bone",
            "meat",
            "rice",
            "bread",
            "छिलका",
            "सब्ज़ी",
            "फल",
            "खाना",
        ),
    ),
    (
        "dry",
        (
            "newspaper",
            "paper",
            "cardboard",
            "carton",
            "glass",
            "bottle",
            "plastic bottle",
            "jar",
            "tin can",
            "aluminium can",
            "aluminum can",
            "steel",
            "aluminium",
            "aluminum",
            "metal",
            "scrap",
            "clothing",
            "clothes",
            "textile",
            "shoe",
            "book",
            "magazine",
            "पेपर",
            "अखबार",
            "समाचारपत्र",
            "कांच",
            "बोतल",
            "कपड़ा",
        ),
    ),
    (
        "reject",
        (
            "polystyrene",
            "styrofoam",
            "thermocol",
            "multilayer",
            "laminate",
            "chip bag",
            "cigarette",
            "chewing gum",
            "पॉलीस्टाइरीन",
            "थर्मोकोल",
        ),
    ),
]

STREAM_ADVICE: dict[str, dict[str, list[str]]] = {
    "wet": {
        "en": [
            "Wet (green) bin or the municipal wet-waste collection.",
            "Compost at home or in your colony if you can.",
            "Keep plastic, foil and rubber bands out.",
        ],
        "hi": [
            "गीला (हरा) डिब्बा या नगर निगम की गीले कचरे की गाड़ी।",
            "हो सके तो घर या कॉलोनी में खाद बनाएँ।",
            "प्लास्टिक, पन्नी और रबर बैंड अलग रखें।",
        ],
    },
    "dry": {
        "en": [
            "Dry (blue) bin, clean and dry.",
            "If it has value, sell or give it to the kabadiwala or a recycler.",
            "Rinse food off first; dirty, wet dry waste is rarely recycled.",
        ],
        "hi": [
            "सूखा (नीला) डिब्बा — साफ़ और सूखा।",
            "कीमत हो तो कबाड़ीवाले या रीसाइक्लर को दें।",
            "पहले खाना धो दें; गीला-गंदा सूखा कचरा कम ही रीसायकल होता है।",
        ],
    },
    "sanitary": {
        "en": [
            "Wrap in newspaper or a marked bag and hand over as sanitary waste.",
            "Never compost it, never mix with dry recyclables, never burn it in the open.",
        ],
        "hi": [
            "अखबार या निशान वाले बैग में लपेटें और सैनिटरी कचरे के रूप में दें।",
            "खाद, सूखे रीसायकल या खुली आग में कभी नहीं।",
        ],
    },
    "ewaste": {
        "en": [
            "Give it to an authorised e-waste collector or a brand take-back point.",
            "Never in household wet or dry waste; do not break or burn it.",
        ],
        "hi": [
            "अधिकृत ई-वेस्ट संग्रहकर्ता या ब्रांड के टेक-बैक केंद्र को दें।",
            "घरेलू गीले या सूखे कचरे में नहीं; तोड़ें या जलाएँ नहीं।",
        ],
    },
    "hazardous": {
        "en": [
            "Keep it separate and take it to a domestic hazardous-waste drop-off.",
            "Never down the drain, never in the wet or dry bin, never burned.",
        ],
        "hi": [
            "अलग रखें और घरेलू खतरनाक कचरा केंद्र पर दें।",
            "नाली, गीले या सूखे डिब्बे में नहीं; जलाएँ नहीं।",
        ],
    },
    "construction": {
        "en": [
            "Construction and demolition waste: book your city's C&D pickup or an authorised debris site.",
            "Not with household wet or dry waste.",
        ],
        "hi": [
            "निर्माण-मलबा: शहर की C&D पिकअप या अधिकृत मलबा स्थल।",
            "घरेलू गीले या सूखे कचरे के साथ नहीं।",
        ],
    },
    "reject": {
        "en": [
            "Residual waste: it goes to the reject / landfill stream.",
            "Next time, refuse it or choose a simpler pack.",
        ],
        "hi": [
            "अवशेष कचरा: रिजेक्ट / लैंडफ़िल में जाता है।",
            "अगली बार मना करें या सरल पैक चुनें।",
        ],
    },
}

# English pages about people, places, companies and media are never waste items,
# even when the title has a waste word ("Philip Glass", "Mobile, Alabama").
_NOT_AN_ITEM = re.compile(
    r"\bborn\b|\bpolitician\b|\bactor\b|\bactress\b|\bsinger\b|\bcomposer\b|\bfootballer\b|\bcricketer\b"
    r"|\balbum\b|\bsong\b|\bnovel\b|\bsurname\b|\bgiven name\b|\btelevision series\b|\bcompany\b"
    r"|\bis a (?:city|town|village|district)\b",
    re.IGNORECASE,
)
# "was an English …", "is an Indian …": a capitalised adjective marks a named person, place or title.
_NAMED_ENTITY = re.compile(r"\b(?:is|was) an? [A-Z][a-z]+(?:-[A-Z][a-z]+)? ")

_live_calls: deque[float] = deque()
_live_lock = Lock()
_indexes_ready = False


def normalize_query(query: str) -> str:
    text = re.sub(r"[^\w\s\u0900-\u097F-]", " ", (query or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def query_wiki_lang(query: str) -> str:
    return "hi" if _DEVANAGARI.search(query or "") else "en"


def ensure_miss_indexes() -> None:
    global _indexes_ready
    if _indexes_ready:
        return
    waste_search_misses.create_index([("status", 1), ("count", -1)])
    _indexes_ready = True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _needle_hit(text: str, needle: str) -> bool:
    if _DEVANAGARI.search(needle):
        return needle in text
    return re.search(rf"\b{re.escape(needle)}s?\b", text) is not None


def stream_for(text: str) -> str | None:
    lower = (text or "").lower()
    for stream, needles in STREAM_NEEDLES:
        if any(_needle_hit(lower, needle) for needle in needles):
            return stream
    return None


def _words(text: str) -> set[str]:
    return {w for w in normalize_query(text).split() if len(w) >= 3}


def clean_extract(text: str) -> str:
    """Drop template residue Wikipedia leaves in some extracts ("साँचा:Chembox …")."""
    lines = [
        line
        for line in (text or "").splitlines()
        if not re.search(r"साँचा:|Template:", line)
    ]
    cleaned = re.sub(r"\s+", " ", " ".join(lines)).strip()
    return cleaned if len(cleaned) >= 40 else ""


def _wiki_query(wiki: str, params: dict[str, str]) -> dict[str, Any] | None:
    try:
        with httpx.Client(
            timeout=HTTP_TIMEOUT_S, headers={"User-Agent": USER_AGENT}
        ) as client:
            response = client.get(
                f"https://{wiki}.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "format": "json",
                    "formatversion": "2",
                    **params,
                },
            )
            if response.status_code >= 400:
                return None
            return response.json()
    except (httpx.HTTPError, ValueError):
        return None


def _search_pages(query: str, wiki: str) -> list[dict[str, Any]]:
    other = "en" if wiki == "hi" else "hi"
    data = _wiki_query(
        wiki,
        {
            "generator": "search",
            "gsrsearch": query,
            "gsrlimit": "5",
            "prop": "extracts|langlinks|info|pageprops",
            "exintro": "1",
            "explaintext": "1",
            "exsentences": "3",
            "lllang": other,
            "inprop": "url",
            "ppprop": "disambiguation",
            "redirects": "1",
        },
    )
    pages = ((data or {}).get("query") or {}).get("pages") or []
    return sorted(pages, key=lambda page: page.get("index", 99))


def _page_extract(title: str, wiki: str) -> dict[str, str] | None:
    data = _wiki_query(
        wiki,
        {
            "titles": title,
            "prop": "extracts|info",
            "exintro": "1",
            "explaintext": "1",
            "exsentences": "3",
            "inprop": "url",
            "redirects": "1",
        },
    )
    pages = ((data or {}).get("query") or {}).get("pages") or []
    if not pages or pages[0].get("missing"):
        return None
    page = pages[0]
    return {
        "title": page.get("title") or title,
        "extract": (page.get("extract") or "").strip(),
        "url": page.get("fullurl") or "",
    }


def pick_page(
    query: str, pages: list[dict[str, Any]]
) -> tuple[dict[str, Any], str] | None:
    """Choose the search result that names a waste item, or None.

    A title naming a waste item wins if it shares a word with the query or is the
    top real (non-disambiguation) result — how Wikipedia answers "thermocol" with
    "Polystyrene". Failing that, an extract hit counts only for a top-two result
    whose title shares a word with the query, so "asdf" never becomes "QWERTY".
    """
    real = [
        p
        for p in pages
        if not (p.get("pageprops") or {}).get("disambiguation")
        and p.get("extract")
        and not _NOT_AN_ITEM.search(p["extract"][:200])
        and not _NAMED_ENTITY.search(p["extract"][:200])
    ]
    query_words = _words(query)

    def other_title(page: dict[str, Any]) -> str:
        return ((page.get("langlinks") or [{}])[0] or {}).get("title") or ""

    for rank, page in enumerate(real):
        titles = f"{page.get('title', '')} {other_title(page)}"
        stream = stream_for(titles)
        if stream and (rank == 0 or query_words & _words(titles)):
            return page, stream
    for page in real[:2]:
        titles = f"{page.get('title', '')} {other_title(page)}"
        if not query_words & _words(titles):
            continue
        stream = stream_for(f"{titles} {page.get('extract', '')}")
        if stream:
            return page, stream
    return None


def lookup_on_web(query: str) -> dict[str, Any] | None:
    """Return an archive document for `query` built from Wikipedia, or None."""
    started = time.monotonic()
    wiki = query_wiki_lang(query)
    other = "en" if wiki == "hi" else "hi"
    choice = pick_page(query, _search_pages(query, wiki))
    if not choice:
        return None
    page, stream = choice
    found = {
        "title": page["title"],
        "extract": page["extract"].strip(),
        "url": page.get("fullurl") or "",
    }
    linked_title = ((page.get("langlinks") or [{}])[0] or {}).get("title") or ""
    linked = None
    if linked_title and time.monotonic() - started < LOOKUP_DEADLINE_S - HTTP_TIMEOUT_S:
        linked = _page_extract(linked_title, other)

    by_lang = {wiki: found, other: linked}
    en = by_lang["en"] or found
    hi = by_lang["hi"] or found
    sources = [
        {"url": src["url"], "title": src["title"]}
        for src in (found, linked)
        if src and src.get("url")
    ]
    norm = normalize_query(query)
    entry_id = "web-" + re.sub(r"[^a-z0-9]+", "-", en["title"].lower()).strip("-")[:70]
    if entry_id == "web-":
        entry_id = "web-" + re.sub(r"\s+", "-", hi["title"])[:70]
    return {
        "id": entry_id,
        "stream": stream,
        "name_en": en["title"],
        "name_hi": hi["title"],
        "aliases_en": [norm] if wiki == "en" else [en["title"].lower()],
        "aliases_hi": [norm]
        if wiki == "hi"
        else ([hi["title"]] if by_lang["hi"] else []),
        "dispose_en": STREAM_ADVICE[stream]["en"],
        "dispose_hi": STREAM_ADVICE[stream]["hi"],
        "snippet_en": (clean_extract(en["extract"]) or clean_extract(found["extract"]))[
            :400
        ],
        "snippet_hi": (clean_extract(hi["extract"]) or clean_extract(en["extract"]))[
            :400
        ],
        "sources": sources,
        "origin": "web",
        "learned_from": norm,
        "learned_at": _now(),
    }


def store_learned(doc: dict[str, Any]) -> dict[str, Any]:
    """Upsert a learned entry, merging aliases if the page was learned before."""
    existing = waste_archive.find_one({"id": doc["id"]}, {"_id": 0}) or {}
    for key in ("aliases_en", "aliases_hi"):
        merged = list(existing.get(key) or [])
        for alias in doc.get(key) or []:
            if alias and alias not in merged:
                merged.append(alias)
        doc[key] = merged
    if existing.get("origin") and existing["origin"] != "web":
        doc["origin"] = existing["origin"]
    upsert_entry(doc)
    return doc


def _take_live_slot() -> bool:
    now = time.monotonic()
    with _live_lock:
        while _live_calls and now - _live_calls[0] > 60:
            _live_calls.popleft()
        if len(_live_calls) >= LIVE_LOOKUPS_PER_MINUTE:
            return False
        _live_calls.append(now)
        return True


def _learn(norm: str, query: str) -> dict[str, Any] | None:
    doc = lookup_on_web(query)
    now = _now()
    if doc:
        stored = store_learned(doc)
        waste_search_misses.update_one(
            {"_id": norm},
            {
                "$set": {
                    "status": "learned",
                    "entry_id": stored["id"],
                    "last_lookup_at": now,
                }
            },
        )
        return stored
    waste_search_misses.update_one(
        {"_id": norm}, {"$set": {"status": "not_found", "last_lookup_at": now}}
    )
    return None


def learn_on_miss(query: str) -> dict[str, Any] | None:
    """Count the miss; look it up live when allowed. Returns the stored entry or None."""
    norm = normalize_query(query)
    if not norm:
        return None
    ensure_miss_indexes()
    now = _now()
    miss = (
        waste_search_misses.find_one_and_update(
            {"_id": norm},
            {
                "$inc": {"count": 1},
                "$set": {
                    "last_seen": now,
                    "query": query.strip()[:120],
                    "wiki": query_wiki_lang(query),
                },
                "$setOnInsert": {"first_seen": now, "status": "pending"},
            },
            upsert=True,
            return_document=True,
        )
        or {}
    )
    if len(norm) < MIN_QUERY_CHARS:
        return None
    last = _aware(miss.get("last_lookup_at"))
    if last and now - last < LIVE_LOOKUP_COOLDOWN:
        return None
    if not _take_live_slot():
        return None
    return _learn(norm, query)


def crawl_search_misses(limit: int = 50) -> dict[str, int]:
    """Weekly pass: retry the most-searched misses that are still unanswered."""
    ensure_miss_indexes()
    cutoff = _now() - CRAWL_RETRY_AFTER
    cursor = (
        waste_search_misses.find(
            {
                "status": {"$ne": "learned"},
                "$or": [
                    {"last_lookup_at": {"$exists": False}},
                    {"last_lookup_at": {"$lt": cutoff}},
                ],
            }
        )
        .sort("count", -1)
        .limit(limit)
    )
    learned = 0
    not_found = 0
    for miss in list(cursor):
        norm = miss["_id"]
        if len(norm) < MIN_QUERY_CHARS:
            continue
        if _learn(norm, miss.get("query") or norm):
            learned += 1
        else:
            not_found += 1
    return {"misses_learned": learned, "misses_not_found": not_found}
