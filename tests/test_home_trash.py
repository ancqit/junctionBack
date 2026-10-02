"""Home trash: MPIN-issued contact token, bucket CRUD, and the PDF download (mongomock, no network)."""

from datetime import datetime, timezone
from unittest.mock import patch

import mongomock
import pytest
from fastapi.testclient import TestClient

from app import home_trash as home_trash_module
from app.catalog_contacts import create_contact_token
from app.login import hash_password
from app.main import app

SECRET = "test-secret-that-is-at-least-32-chars-long"
PHONE = "+919812345678"
MPIN = "4826"

PEEL = {
    "id": "peel",
    "stream": "wet",
    "name_en": "Fruit or vegetable peel",
    "name_hi": "फल या सब्ज़ी का छिलका",
    "dispose_en": ["Home or community compost.", "Never the dry bag or the drain."],
    "dispose_hi": ["घर या सामुदायिक खाद।", "सूखे बैग या नाली में कभी नहीं।"],
    "snippet_en": "Compost it.",
    "sources": [{"url": "https://en.wikipedia.org/wiki/Compost", "title": "Compost"}],
}
BATTERY = {
    "id": "battery",
    "stream": "hazardous",
    "name_en": "Dead battery (AA, button, lithium)",
    "name_hi": "खत्म बैटरी (AA, बटन, लिथियम)",
    "dispose_en": ["Battery collection / hazardous drop."],
    "dispose_hi": ["बैटरी संग्रह / खतरनाक ड्रॉप।"],
}


@pytest.fixture
def db():
    client = mongomock.MongoClient()
    database = client["junction_test"]
    contacts = database["catalog_contacts"]
    archive = database["waste_archive"]
    bucket = database["home_trash"]
    archive.insert_many([dict(PEEL), dict(BATTERY)])
    contacts.insert_one(
        {
            "phone_number": PHONE,
            "email": "reena@example.com",
            "display_name": "Reena Sharma",
            "verified": True,
            "mpin_hash": hash_password(MPIN),
            "mpin_set_at": datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
            "order_ids": [],
        }
    )
    with (
        patch("app.catalog_contacts.catalog_contacts", contacts),
        patch("app.catalog_contacts.JWT_SECRET", SECRET),
        patch.object(home_trash_module, "home_trash", bucket),
        patch.object(home_trash_module, "waste_archive", archive),
        patch.object(home_trash_module, "_indexes_ready", False),
    ):
        yield {"contacts": contacts, "archive": archive, "bucket": bucket}


@pytest.fixture
def client():
    return TestClient(app)


def _login(client: TestClient) -> dict:
    response = client.post(
        "/auth/catalog-contacts/mpin/login", json={"phone_number": PHONE, "mpin": MPIN}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _auth(token: str) -> dict:
    return {"X-Junction-Contact": token}


def test_mpin_login_issues_contact_token_and_keeps_jtoday_fields(db, client):
    body = _login(client)
    assert body["verified"] is True
    assert body["has_mpin"] is True
    assert body["phone_number"] == PHONE
    assert body["email"] == "reena@example.com"
    assert body["display_name"] == "Reena Sharma"
    assert body["access_token"]
    assert body["expires_in"] > 0


def test_wrong_mpin_is_rejected(db, client):
    response = client.post(
        "/auth/catalog-contacts/mpin/login",
        json={"phone_number": PHONE, "mpin": "0000"},
    )
    assert response.status_code == 401


def test_bucket_requires_contact_token(db, client):
    assert client.get("/earth/home-trash").status_code == 401
    assert (
        client.get("/earth/home-trash", headers=_auth("not-a-jwt")).status_code == 401
    )
    assert client.get("/earth/home-trash/pdf").status_code == 401


def test_resetting_mpin_invalidates_old_tokens(db, client):
    token = _login(client)["access_token"]
    assert client.get("/earth/home-trash", headers=_auth(token)).status_code == 200
    db["contacts"].update_one(
        {"phone_number": PHONE},
        {"$set": {"mpin_set_at": datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)}},
    )
    response = client.get("/earth/home-trash", headers=_auth(token))
    assert response.status_code == 401
    assert "MPIN" in response.json()["detail"]


def test_unverified_contact_token_is_rejected(db, client):
    token = _login(client)["access_token"]
    db["contacts"].update_one({"phone_number": PHONE}, {"$set": {"verified": False}})
    assert client.get("/earth/home-trash", headers=_auth(token)).status_code == 401


def test_add_list_remove_and_empty(db, client):
    headers = _auth(_login(client)["access_token"])

    added = client.post("/earth/home-trash", json={"entry_id": "peel"}, headers=headers)
    assert added.status_code == 200
    assert added.json()["title_hi"] == "फल या सब्ज़ी का छिलका"
    assert added.json()["stream"] == "wet"

    again = client.post("/earth/home-trash", json={"entry_id": "peel"}, headers=headers)
    assert again.status_code == 200
    assert again.json()["added_at"] == added.json()["added_at"]

    client.post("/earth/home-trash", json={"entry_id": "battery"}, headers=headers)
    listed = client.get("/earth/home-trash", headers=headers).json()
    assert listed["count"] == 2
    assert {item["entry_id"] for item in listed["items"]} == {"peel", "battery"}

    assert client.delete("/earth/home-trash/peel", headers=headers).status_code == 204
    assert [
        item["entry_id"]
        for item in client.get("/earth/home-trash", headers=headers).json()["items"]
    ] == ["battery"]

    assert client.delete("/earth/home-trash", headers=headers).status_code == 204
    assert client.get("/earth/home-trash", headers=headers).json()["count"] == 0


def test_snapshot_survives_archive_edits(db, client):
    headers = _auth(_login(client)["access_token"])
    client.post("/earth/home-trash", json={"entry_id": "peel"}, headers=headers)
    db["archive"].update_one({"id": "peel"}, {"$set": {"name_en": "Changed"}})
    assert client.get("/earth/home-trash", headers=headers).json()["items"][0][
        "title_en"
    ] == ("Fruit or vegetable peel")


def test_unknown_entry_is_404(db, client):
    headers = _auth(_login(client)["access_token"])
    response = client.post(
        "/earth/home-trash", json={"entry_id": "no-such-thing"}, headers=headers
    )
    assert response.status_code == 404


def test_bucket_is_capped(db, client):
    headers = _auth(_login(client)["access_token"])
    with patch.object(home_trash_module, "MAX_ITEMS", 1):
        assert (
            client.post(
                "/earth/home-trash", json={"entry_id": "peel"}, headers=headers
            ).status_code
            == 200
        )
        response = client.post(
            "/earth/home-trash", json={"entry_id": "battery"}, headers=headers
        )
    assert response.status_code == 409


def test_buckets_are_private_per_contact(db, client):
    headers = _auth(_login(client)["access_token"])
    client.post("/earth/home-trash", json={"entry_id": "peel"}, headers=headers)
    other_id = (
        db["contacts"]
        .insert_one(
            {
                "phone_number": "+919800000001",
                "email": "o@example.com",
                "verified": True,
            }
        )
        .inserted_id
    )
    other_token = create_contact_token(db["contacts"].find_one({"_id": other_id}))
    assert (
        client.get("/earth/home-trash", headers=_auth(other_token)).json()["count"] == 0
    )


@pytest.mark.parametrize("lang", ["en", "hi"])
def test_pdf_download(db, client, lang):
    headers = _auth(_login(client)["access_token"])
    client.post("/earth/home-trash", json={"entry_id": "peel"}, headers=headers)
    client.post("/earth/home-trash", json={"entry_id": "battery"}, headers=headers)
    response = client.get(f"/earth/home-trash/pdf?lang={lang}", headers=headers)
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert "no-store" in response.headers["cache-control"]


def test_empty_bucket_still_downloads(db, client):
    headers = _auth(_login(client)["access_token"])
    response = client.get("/earth/home-trash/pdf", headers=headers)
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
