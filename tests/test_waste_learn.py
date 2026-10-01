"""Learn-on-miss for the jEarth waste archive (Wikipedia mocked, Mongo via mongomock)."""

from datetime import timedelta
from unittest.mock import patch

import mongomock
import pytest
from fastapi.testclient import TestClient

from app import waste_learn
from app.main import app


def page(index, title, extract, *, link="", url=None, disambiguation=False):
    doc = {
        "index": index,
        "title": title,
        "extract": extract,
        "fullurl": url or f"https://wiki/{title}",
    }
    if link:
        doc["langlinks"] = [{"lang": "x", "title": link}]
    if disambiguation:
        doc["pageprops"] = {"disambiguation": ""}
    return doc


MOBILE_SEARCH = [
    page(1, "Mobile", "Mobile may refer to:", disambiguation=True),
    page(
        2,
        "Mobile app",
        "A mobile app is a computer program designed to run on a phone.",
        link="मोबाइल अनुप्रयोग",
    ),
    page(
        3, "Mobile phone", "A mobile phone is a portable telephone.", link="मोबाइल फ़ोन"
    ),
    page(
        4,
        "Mobile, Alabama",
        "Mobile is a city and the county seat of Mobile County, Alabama.",
    ),
]
HINDI_MOBILE_SEARCH = [
    page(1, "मोबाइल फ़ोन", "मोबाइल फ़ोन एक सुवाह्य टेलीफ़ोन है।", link="Mobile phone")
]


def wiki_stub(searches, extracts=None, calls=None):
    extracts = extracts or {}

    def fake(wiki, params):
        if calls is not None:
            calls.append((wiki, params.get("gsrsearch") or params.get("titles")))
        if "gsrsearch" in params:
            return {"query": {"pages": searches.get((wiki, params["gsrsearch"]), [])}}
        found = extracts.get((wiki, params["titles"]))
        return {"query": {"pages": [found] if found else [{"missing": True}]}}

    return fake


@pytest.fixture
def db():
    mock_db = mongomock.MongoClient().db
    with (
        patch("app.waste_learn.waste_archive", mock_db.waste_archive),
        patch("app.waste_crawler.waste_archive", mock_db.waste_archive),
        patch("app.database.waste_archive", mock_db.waste_archive),
        patch("app.waste_learn.waste_search_misses", mock_db.waste_search_misses),
        patch.object(waste_learn, "_indexes_ready", True),
        patch("app.waste_crawler._indexes_ready", True),
    ):
        waste_learn._live_calls.clear()
        mock_db.waste_archive.insert_one(
            {"id": "battery", "name_en": "Dead battery", "aliases_en": ["battery"]}
        )
        yield mock_db


def test_pick_page_prefers_a_title_naming_a_waste_item():
    picked = waste_learn.pick_page("mobile", MOBILE_SEARCH)
    assert picked is not None
    assert picked[0]["title"] == "Mobile phone"
    assert picked[1] == "ewaste"


def test_pick_page_accepts_the_top_result_for_a_redirected_name():
    pages = [
        page(
            1, "Polystyrene", "Polystyrene is a synthetic polymer.", link="पॉलीस्टाइरीन"
        )
    ]
    assert waste_learn.pick_page("thermocol", pages)[1] == "reject"


def test_pick_page_rejects_junk_people_and_places():
    assert (
        waste_learn.pick_page(
            "asdf",
            [
                page(1, "ASDF", "ASDF may refer to:", disambiguation=True),
                page(2, "QWERTY", "QWERTY is a keyboard layout."),
            ],
        )
        is None
    )
    assert (
        waste_learn.pick_page(
            "philip",
            [page(1, "Philip Glass", "Philip Glass (born 1937) is a composer.")],
        )
        is None
    )
    assert (
        waste_learn.pick_page(
            "john",
            [
                page(1, "John the Apostle", "John was an apostle."),
                page(
                    2, "John Glass", "John Glass was an English cricket administrator."
                ),
            ],
        )
        is None
    )


def test_clean_extract_drops_template_residue():
    assert waste_learn.clean_extract("साँचा:Chembox लघुनाम\n\n") == ""
    text = "Polystyrene is a synthetic polymer made from monomers of styrene."
    assert waste_learn.clean_extract(f"Template:Infobox\n{text}") == text


def test_miss_is_learned_from_the_web_and_returned(db):
    searches = {("en", "mobile"): MOBILE_SEARCH}
    extracts = {
        ("hi", "मोबाइल फ़ोन"): {
            "title": "मोबाइल फ़ोन",
            "extract": "मोबाइल फ़ोन एक टेलीफ़ोन है।",
            "fullurl": "https://hi.wiki/m",
        }
    }
    with patch.object(waste_learn, "_wiki_query", wiki_stub(searches, extracts)):
        response = TestClient(app).get(
            "/earth/waste/search", params={"q": "mobile", "lang": "hi"}
        )

    assert response.status_code == 200
    [hit] = response.json()["results"]
    assert hit["title"] == "मोबाइल फ़ोन"
    assert hit["stream"] == "ewaste"
    assert hit["origin"] == "web"
    assert hit["dispose"] == waste_learn.STREAM_ADVICE["ewaste"]["hi"]
    assert [s["url"] for s in hit["sources"]] == [
        "https://wiki/Mobile phone",
        "https://hi.wiki/m",
    ]

    stored = db.waste_archive.find_one({"id": "web-mobile-phone"})
    assert stored["aliases_en"] == ["mobile"]
    assert stored["aliases_hi"] == ["मोबाइल फ़ोन"]
    assert db.waste_search_misses.find_one({"_id": "mobile"})["status"] == "learned"


def test_learned_entry_is_found_next_time_without_the_web(db):
    with patch.object(
        waste_learn, "_wiki_query", wiki_stub({("en", "mobile"): MOBILE_SEARCH})
    ):
        TestClient(app).get("/earth/waste/search", params={"q": "mobile"})
    with patch.object(
        waste_learn, "_wiki_query", side_effect=AssertionError("no web call expected")
    ):
        response = TestClient(app).get("/earth/waste/search", params={"q": "mobile"})
    assert [h["id"] for h in response.json()["results"]] == ["web-mobile-phone"]


def test_hindi_query_searches_hindi_wikipedia_and_merges_aliases(db):
    calls = []
    searches = {("en", "mobile"): MOBILE_SEARCH, ("hi", "मोबाइल"): HINDI_MOBILE_SEARCH}
    extracts = {
        ("en", "Mobile phone"): {
            "title": "Mobile phone",
            "extract": "A mobile phone is a telephone.",
            "fullurl": "https://wiki/Mobile phone",
        }
    }
    with patch.object(waste_learn, "_wiki_query", wiki_stub(searches, extracts, calls)):
        waste_learn.learn_on_miss("mobile")
        entry = waste_learn.learn_on_miss("मोबाइल")

    assert ("hi", "मोबाइल") in calls
    assert entry["id"] == "web-mobile-phone"
    stored = db.waste_archive.find_one({"id": "web-mobile-phone"})
    assert "मोबाइल" in stored["aliases_hi"]
    assert stored["aliases_en"] == ["mobile", "mobile phone"]
    assert db.waste_archive.count_documents({"origin": "web"}) == 1


def test_unknown_query_is_counted_and_not_retried_within_a_day(db):
    calls = []
    with patch.object(waste_learn, "_wiki_query", wiki_stub({}, calls=calls)):
        assert waste_learn.learn_on_miss("asdf") is None
        assert waste_learn.learn_on_miss("ASDF!") is None

    miss = db.waste_search_misses.find_one({"_id": "asdf"})
    assert miss["count"] == 2
    assert miss["status"] == "not_found"
    assert len(calls) == 1
    assert db.waste_archive.count_documents({"origin": "web"}) == 0


def test_short_queries_are_counted_but_never_looked_up(db):
    with patch.object(
        waste_learn, "_wiki_query", side_effect=AssertionError("no web call expected")
    ):
        assert waste_learn.learn_on_miss("mo") is None
    assert db.waste_search_misses.find_one({"_id": "mo"})["count"] == 1


def test_live_lookups_are_capped_per_minute(db):
    calls = []
    with (
        patch.object(waste_learn, "LIVE_LOOKUPS_PER_MINUTE", 2),
        patch.object(waste_learn, "_wiki_query", wiki_stub({}, calls=calls)),
    ):
        for q in ("aaa", "bbb", "ccc"):
            waste_learn.learn_on_miss(q)
    assert len(calls) == 2


def test_weekly_crawl_retries_the_most_searched_misses(db):
    now = waste_learn._now()
    db.waste_search_misses.insert_many(
        [
            {
                "_id": "mobile",
                "query": "mobile",
                "count": 9,
                "status": "not_found",
                "last_lookup_at": now - timedelta(days=7),
            },
            {"_id": "asdf", "query": "asdf", "count": 3, "status": "pending"},
            {
                "_id": "glass",
                "query": "glass",
                "count": 5,
                "status": "not_found",
                "last_lookup_at": now,
            },
            {"_id": "paper", "query": "paper", "count": 4, "status": "learned"},
        ]
    )
    calls = []
    with patch.object(
        waste_learn,
        "_wiki_query",
        wiki_stub({("en", "mobile"): MOBILE_SEARCH}, calls=calls),
    ):
        stats = waste_learn.crawl_search_misses()

    assert stats == {"misses_learned": 1, "misses_not_found": 1}
    searched = [q for _, q in calls if q in {"mobile", "asdf", "glass", "paper"}]
    assert searched == ["mobile", "asdf"]
    assert db.waste_search_misses.find_one({"_id": "mobile"})["status"] == "learned"
