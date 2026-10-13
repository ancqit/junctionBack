"""Junction ID: check first, create with an MPIN, SMS only as the backup (mongomock, no network)."""

from datetime import datetime, timezone
from unittest.mock import patch

import mongomock
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import junction_id as jid
from app.login import hash_password
from app.main import app
from app.rate_limit import limiter

SECRET = "test-secret-that-is-at-least-32-chars-long"
NEW = "+919800000001"
MONSTER = "+919800000002"
BLOG = "+919800000003"
TWO_APPS = "+919800000004"
ORDER_ONLY = "+919800000005"


@pytest.fixture
def db():
    database = mongomock.MongoClient()["junction_test"]
    stores = {name: database[name] for name in ("identities", "users", "catalog_contacts", "monster_profiles", "blog_accounts")}
    stores["monster_profiles"].insert_one(
        {"phone_number": MONSTER, "name": "Arjun", "profile_name": "arjun", "mpin_hash": hash_password("1357")}
    )
    stores["blog_accounts"].insert_one(
        {"phone_number": BLOG, "display_name": "User 0003", "pin_hash": hash_password("K7★#")}
    )
    stores["catalog_contacts"].insert_one(
        {
            "phone_number": TWO_APPS,
            "display_name": "Reena Sharma",
            "verified": True,
            "mpin_hash": hash_password("2468"),
            "mpin_set_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        }
    )
    stores["monster_profiles"].insert_one(
        {"phone_number": TWO_APPS, "name": "Reena", "profile_name": "reena", "mpin_hash": hash_password("9999")}
    )
    stores["catalog_contacts"].insert_one({"phone_number": ORDER_ONLY, "email": "o@example.com", "order_ids": ["x"]})
    with (
        patch.object(jid, "identities", stores["identities"]),
        patch.object(jid, "users", stores["users"]),
        patch.object(jid, "catalog_contacts", stores["catalog_contacts"]),
        patch.object(jid, "monster_profiles", stores["monster_profiles"]),
        patch.object(jid, "blog_accounts", stores["blog_accounts"]),
        patch.object(jid, "JWT_SECRET", SECRET),
        patch.object(jid, "_indexes_ready", False),
        patch.object(limiter, "enabled", False),
    ):
        yield stores


@pytest.fixture
def client():
    return TestClient(app)


def _lookup(client, phone):
    response = client.post("/auth/id/lookup", json={"phone_number": phone})
    assert response.status_code == 200, response.text
    return response.json()


def _login(client, phone, mpin):
    return client.post("/auth/id/login", json={"phone_number": phone, "mpin": mpin, "app": "earth"})


def _me(client, token):
    return client.get("/auth/id/me", headers={"X-Junction-Id": token})


def test_new_phone_is_checked_then_created_with_just_an_mpin(db, client):
    assert _lookup(client, NEW) == {"phone_number": NEW, "exists": False, "pin_kind": None}

    created = client.post(
        "/auth/id/create", json={"phone_number": "98000 00001", "mpin": "4826", "display_name": " Asha ", "app": "earth"}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["phone_number"] == NEW and body["display_name"] == "Asha" and body["verified"] is False

    assert _lookup(client, NEW) == {"phone_number": NEW, "exists": True, "pin_kind": "mpin"}
    doc = db["identities"].find_one({"phone_number": NEW})
    assert doc["mpin_source"] == "self" and doc["created_via"] == "earth"

    me = _me(client, body["access_token"]).json()
    assert me["phone_number"] == NEW and me["apps"] == {"staff": False, "contact": False, "monster": False, "blog": False}

    assert _login(client, NEW, "4826").status_code == 200
    assert _login(client, NEW, "1111").status_code == 401


def test_create_refuses_any_phone_that_already_has_a_junction_account(db, client):
    assert client.post("/auth/id/create", json={"phone_number": NEW, "mpin": "4826"}).status_code == 201
    again = client.post("/auth/id/create", json={"phone_number": NEW, "mpin": "0000"})
    assert again.status_code == 409
    for phone in (MONSTER, BLOG, TWO_APPS):
        assert client.post("/auth/id/create", json={"phone_number": phone, "mpin": "0000"}).status_code == 409
    assert _login(client, NEW, "4826").status_code == 200


def test_an_order_contact_without_an_mpin_can_still_create(db, client):
    assert _lookup(client, ORDER_ONLY)["exists"] is False
    assert client.post("/auth/id/create", json={"phone_number": ORDER_ONLY, "mpin": "4826"}).status_code == 201


def test_old_app_mpin_is_adopted_on_first_sign_in(db, client):
    assert _lookup(client, MONSTER) == {"phone_number": MONSTER, "exists": True, "pin_kind": "mpin"}
    response = _login(client, MONSTER, "1357")
    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == "Arjun"
    doc = db["identities"].find_one({"phone_number": MONSTER})
    assert doc["mpin_source"] == "adopted:monster" and doc["verified"] is False

    me = _me(client, response.json()["access_token"]).json()
    assert me["apps"]["monster"] is True and me["apps"]["blog"] is False
    assert _login(client, MONSTER, "1357").status_code == 200


def test_first_old_mpin_used_wins_when_apps_disagree(db, client):
    response = _login(client, TWO_APPS, "9999")
    assert response.status_code == 200
    assert db["identities"].find_one({"phone_number": TWO_APPS})["mpin_source"] == "adopted:monster"
    assert _login(client, TWO_APPS, "2468").status_code == 401
    assert _login(client, TWO_APPS, "9999").status_code == 200


def test_adopting_a_verified_jtoday_contact_keeps_it_verified(db, client):
    response = _login(client, TWO_APPS, "2468")
    assert response.status_code == 200
    assert response.json()["verified"] is True and response.json()["display_name"] == "Reena Sharma"


def test_blog_keypad_pin_upgrades_to_a_junction_mpin(db, client):
    assert _lookup(client, BLOG) == {"phone_number": BLOG, "exists": True, "pin_kind": "blog_pin"}
    wrong = client.post("/auth/id/upgrade", json={"phone_number": BLOG, "old_pin": "AAAA", "mpin": "5555"})
    assert wrong.status_code == 401
    upgraded = client.post("/auth/id/upgrade", json={"phone_number": BLOG, "old_pin": "K7★#", "mpin": "5555", "app": "blog"})
    assert upgraded.status_code == 200, upgraded.text
    assert db["identities"].find_one({"phone_number": BLOG})["mpin_source"] == "adopted:blog"
    assert _login(client, BLOG, "5555").status_code == 200
    again = client.post("/auth/id/upgrade", json={"phone_number": BLOG, "old_pin": "K7★#", "mpin": "6666"})
    assert again.status_code == 409


def test_five_wrong_mpins_lock_the_phone_even_for_the_right_one(db, client):
    client.post("/auth/id/create", json={"phone_number": NEW, "mpin": "4826"})
    for _ in range(4):
        assert _login(client, NEW, "0000").status_code == 401
    assert _login(client, NEW, "0000").status_code == 429
    assert _login(client, NEW, "4826").status_code == 429


def test_wrong_old_app_mpins_count_toward_the_same_lockout(db, client):
    for _ in range(4):
        assert _login(client, MONSTER, "0000").status_code == 401
    assert _login(client, MONSTER, "0000").status_code == 429
    assert _login(client, MONSTER, "1357").status_code == 429
    assert _lookup(client, MONSTER)["exists"] is True


def test_a_right_mpin_clears_earlier_misses(db, client):
    client.post("/auth/id/create", json={"phone_number": NEW, "mpin": "4826"})
    for _ in range(4):
        _login(client, NEW, "0000")
    assert _login(client, NEW, "4826").status_code == 200
    for _ in range(4):
        assert _login(client, NEW, "0000").status_code == 401


def test_sms_is_the_backup_for_a_forgotten_mpin(db, client):
    created = client.post("/auth/id/create", json={"phone_number": NEW, "mpin": "4826"}).json()
    for _ in range(5):
        _login(client, NEW, "0000")

    with patch.object(jid, "confirm_catalog_otp", return_value={"display_name": "Junction customer"}) as confirm:
        reset = client.post(
            "/auth/id/reset",
            json={"phone_number": NEW, "otp": "123456", "session_info": "s", "mpin": "7070", "display_name": "Asha"},
        )
    assert reset.status_code == 200, reset.text
    confirm.assert_called_once_with(NEW, "s", "123456")
    assert reset.json()["verified"] is True and reset.json()["display_name"] == "Asha"

    assert _login(client, NEW, "7070").status_code == 200
    assert _login(client, NEW, "4826").status_code == 401
    assert _me(client, created["access_token"]).status_code == 401
    assert _me(client, reset.json()["access_token"]).status_code == 200


def test_a_bad_sms_code_changes_nothing(db, client):
    client.post("/auth/id/create", json={"phone_number": NEW, "mpin": "4826"})
    with patch.object(jid, "confirm_catalog_otp", side_effect=HTTPException(status_code=401, detail="Invalid OTP")):
        reset = client.post(
            "/auth/id/reset", json={"phone_number": NEW, "otp": "000000", "session_info": "s", "mpin": "7070"}
        )
    assert reset.status_code == 401
    assert _login(client, NEW, "4826").status_code == 200


def test_sms_reset_also_works_for_a_brand_new_phone(db, client):
    with patch.object(jid, "confirm_catalog_otp", return_value={}):
        reset = client.post("/auth/id/reset", json={"phone_number": NEW, "otp": "123456", "session_info": "s", "mpin": "7070"})
    assert reset.status_code == 200
    assert db["identities"].find_one({"phone_number": NEW})["mpin_source"] == "sms"


def test_me_rejects_other_junction_tokens(db, client):
    from app.catalog_contacts import create_contact_token

    with patch("app.catalog_contacts.JWT_SECRET", SECRET):
        contact_token = create_contact_token({"_id": "64b000000000000000000001"})
    assert _me(client, contact_token).status_code == 401
    assert client.get("/auth/id/me").status_code == 401
