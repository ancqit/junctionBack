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
from app.rate_limit import limiter

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
        patch.object(limiter, "enabled", False),
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


NEW_PHONE = "+919800000042"


def _create(client: TestClient, phone: str = NEW_PHONE, mpin: str = "1357", name: str | None = "Asha"):
    body = {"phone_number": phone, "mpin": mpin}
    if name:
        body["display_name"] = name
    return client.post("/auth/catalog-contacts/mpin/create", json=body)


def _unlock(client: TestClient, phone: str, mpin: str):
    return client.post("/auth/catalog-contacts/mpin/unlock", json={"phone_number": phone, "mpin": mpin})


def test_open_create_gives_a_working_home_trash_without_email(db, client):
    response = _create(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verified"] is False
    assert body["email"] == ""
    assert body["display_name"] == "Asha"
    headers = _auth(body["access_token"])
    assert client.post("/earth/home-trash", json={"entry_id": "peel"}, headers=headers).status_code == 200
    assert client.get("/earth/home-trash/pdf", headers=headers).content.startswith(b"%PDF")

    unlocked = _unlock(client, NEW_PHONE, "1357")
    assert unlocked.status_code == 200
    assert client.get("/earth/home-trash", headers=_auth(unlocked.json()["access_token"])).json()["count"] == 1


def test_open_create_never_overwrites_an_existing_mpin(db, client):
    assert _create(client, phone=PHONE).status_code == 409
    assert _create(client).status_code == 200
    assert _create(client, mpin="9999").status_code == 409
    assert _unlock(client, NEW_PHONE, "1357").status_code == 200


def test_open_create_refuses_verified_contacts_without_mpin(db, client):
    db["contacts"].insert_one({"phone_number": NEW_PHONE, "email": "v@example.com", "verified": True})
    assert _create(client).status_code == 409


def test_open_create_on_order_contact_hides_order_email_and_name(db, client):
    db["contacts"].insert_one(
        {"phone_number": NEW_PHONE, "email": "buyer@example.com", "display_name": "Order Name", "order_ids": ["x"]}
    )
    body = _create(client, name=None).json()
    assert body["email"] == ""
    assert body["display_name"] is None
    stored = db["contacts"].find_one({"phone_number": NEW_PHONE})
    assert stored["display_name"] == "Order Name"
    assert stored["email"] == "buyer@example.com"


def test_self_set_account_cannot_use_jtoday_login(db, client):
    _create(client)
    response = client.post("/auth/catalog-contacts/mpin/login", json={"phone_number": NEW_PHONE, "mpin": "1357"})
    assert response.status_code == 401


def test_unlock_accepts_jtoday_mpin(db, client):
    body = _unlock(client, PHONE, MPIN).json()
    assert body["verified"] is True
    assert body["email"] == "reena@example.com"
    assert body["access_token"]


def test_five_wrong_mpins_lock_the_phone_then_unlock_after_expiry(db, client):
    codes = [_unlock(client, PHONE, "0000").status_code for _ in range(5)]
    assert codes == [401, 401, 401, 401, 429]
    locked = _unlock(client, PHONE, MPIN)
    assert locked.status_code == 429
    assert "Try again" in locked.json()["detail"]

    db["contacts"].update_one(
        {"phone_number": PHONE}, {"$set": {"mpin_locked_until": datetime(2020, 1, 1, tzinfo=timezone.utc)}}
    )
    assert _unlock(client, PHONE, MPIN).status_code == 200
    stored = db["contacts"].find_one({"phone_number": PHONE})
    assert "mpin_locked_until" not in stored
    assert "mpin_failed_count" not in stored


def test_jtoday_login_shares_the_lockout(db, client):
    for _ in range(5):
        client.post("/auth/catalog-contacts/mpin/login", json={"phone_number": PHONE, "mpin": "0000"})
    response = client.post("/auth/catalog-contacts/mpin/login", json={"phone_number": PHONE, "mpin": MPIN})
    assert response.status_code == 429


def test_a_correct_mpin_resets_the_wrong_count(db, client):
    for _ in range(4):
        _unlock(client, PHONE, "0000")
    assert _unlock(client, PHONE, MPIN).status_code == 200
    assert _unlock(client, PHONE, "0000").status_code == 401
