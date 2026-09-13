"""DigiLocker OAuth (Meri Pehchaan / API Setu partner flow).

Spec: DigiLocker Authorized Partner API Specification v2.x
  authorize: /public/oauth2/1/authorize
  token:     /public/oauth2/2/token   (v1 token removed in OpenID Connect revision)
  user:      /public/oauth2/1/user

Partner onboarding: https://apisetu.gov.in/digilocker
"""

import base64
import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated
from urllib.parse import urlencode

import httpx
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .access_control import ensure_shop_access, get_shop_by_store_id
from .database import digilocker_states, shops, users
from .login import get_current_user


router = APIRouter(prefix="/auth/digilocker", tags=["digilocker"])

DIGILOCKER_CLIENT_ID = os.getenv("DIGILOCKER_CLIENT_ID", "")
DIGILOCKER_CLIENT_SECRET = os.getenv("DIGILOCKER_CLIENT_SECRET", "")
DIGILOCKER_REDIRECT_URI = os.getenv("DIGILOCKER_REDIRECT_URI", "")
DIGILOCKER_SUCCESS_REDIRECT = os.getenv(
    "DIGILOCKER_SUCCESS_REDIRECT",
    "https://www.junction.website/back-office?digilocker=verified",
)
DIGILOCKER_AUTHORIZE_URL = os.getenv(
    "DIGILOCKER_AUTHORIZE_URL",
    "https://digilocker.meripehchaan.gov.in/public/oauth2/1/authorize",
)
# Meri Pehchaan / API Setu OpenID Connect uses /oauth2/2/token (v1 removed).
DIGILOCKER_TOKEN_URL = os.getenv(
    "DIGILOCKER_TOKEN_URL",
    "https://digilocker.meripehchaan.gov.in/public/oauth2/2/token",
)
DIGILOCKER_USER_URL = os.getenv(
    "DIGILOCKER_USER_URL",
    "https://digilocker.meripehchaan.gov.in/public/oauth2/1/user",
)
STATE_EXPIRE_MINUTES = 10
_PHONE_DIGITS_RE = re.compile(r"\D+")


class DigiLockerConnectResponse(BaseModel):
    authorization_url: str


class DigiLockerCallbackResponse(BaseModel):
    message: str
    digilocker_verified: bool
    profile_complete: bool


def require_configuration() -> None:
    if not all((DIGILOCKER_CLIENT_ID, DIGILOCKER_CLIENT_SECRET, DIGILOCKER_REDIRECT_URI)):
        raise HTTPException(
            status_code=503,
            detail="DigiLocker partner client ID, secret, and redirect URI are not configured",
        )


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for S256 PKCE."""
    verifier = secrets.token_urlsafe(64)
    if len(verifier) < 43:
        verifier = (verifier + secrets.token_urlsafe(32))[:128]
    elif len(verifier) > 128:
        verifier = verifier[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _verified_mobile(user: dict) -> str | None:
    raw = (user.get("phone_number") or "").strip()
    if not raw:
        return None
    digits = _PHONE_DIGITS_RE.sub("", raw)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) == 10:
        return digits
    return None


def _b64url_json(segment: str) -> dict | None:
    try:
        padded = segment + "=" * (-len(segment) % 4)
        payload = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(payload.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _identity_from_id_token(id_token: str | None) -> dict:
    """Best-effort claims from DigiLocker OpenID id_token when /user is unavailable."""
    if not id_token or id_token.count(".") < 2:
        return {}
    claims = _b64url_json(id_token.split(".")[1]) or {}
    digilocker_id = (
        claims.get("digilockerid")
        or claims.get("user_sso_id")
        or claims.get("sub")
    )
    name = claims.get("name") or claims.get("given_name") or claims.get("preferred_username")
    dob = claims.get("birthdate") or claims.get("dob")
    gender = claims.get("gender")
    out: dict = {}
    if digilocker_id:
        out["digilockerid"] = str(digilocker_id)
    if name:
        out["name"] = str(name)
    if dob:
        out["dob"] = str(dob).replace("/", "").replace("-", "")
    if gender:
        out["gender"] = str(gender)
    return out


def _failure_redirect(reason: str, shop_id: str | None = None) -> RedirectResponse:
    """Send the owner back to junction.website instead of a raw API JSON error page."""
    base = DIGILOCKER_SUCCESS_REDIRECT.strip() or "https://www.junction.website/back-office"
    # Force status=failed even if the success URL already has digilocker=verified.
    if "digilocker=" in base:
        base = re.sub(r"digilocker=[^&]*", "digilocker=failed", base)
    else:
        sep = "&" if "?" in base else "?"
        base = f"{base}{sep}digilocker=failed"
    params: dict[str, str] = {"reason": reason[:160]}
    if shop_id:
        params["shop_id"] = str(shop_id)
    return RedirectResponse(url=f"{base}&{urlencode(params)}", status_code=302)


@router.get("/connect", response_model=DigiLockerConnectResponse)
def connect_digilocker(
    current_user: Annotated[dict, Depends(get_current_user)],
    shop_id: Annotated[str | None, Query(max_length=80)] = None,
    store_id: Annotated[str | None, Query(max_length=80, description="Alias of shop_id")] = None,
) -> DigiLockerConnectResponse:
    require_configuration()
    if not current_user.get("mobile_verified", bool(current_user.get("phone_number"))):
        raise HTTPException(status_code=403, detail="Verify a mobile number before connecting DigiLocker")

    resolved_shop_id = (shop_id or store_id or "").strip() or None
    if resolved_shop_id:
        shop = get_shop_by_store_id(resolved_shop_id)
        ensure_shop_access(current_user, shop)
        resolved_shop_id = str(shop["_id"])

    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = _pkce_pair()
    now = datetime.now(timezone.utc)
    digilocker_states.create_index("expires_at", expireAfterSeconds=0)
    digilocker_states.insert_one(
        {
            "state": state,
            "user_id": current_user["_id"],
            "shop_id": resolved_shop_id,
            "code_verifier": code_verifier,
            "created_at": now,
            "expires_at": now + timedelta(minutes=STATE_EXPIRE_MINUTES),
        }
    )
    query_params: dict[str, str] = {
        "response_type": "code",
        "client_id": DIGILOCKER_CLIENT_ID,
        "redirect_uri": DIGILOCKER_REDIRECT_URI,
        "state": state,
        "scope": "openid",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    mobile = _verified_mobile(current_user)
    if mobile:
        # Trusted partner hint — DigiLocker may skip mobile OTP when registered for it.
        query_params["verified_mobile"] = mobile
    query = urlencode(query_params)
    return DigiLockerConnectResponse(authorization_url=f"{DIGILOCKER_AUTHORIZE_URL}?{query}")


@router.get("/callback")
def digilocker_callback(
    state: str = Query(min_length=20, max_length=200),
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """OAuth redirect target. Always prefer sending the browser back to junction.website."""
    shop_id_hint: str | None = None
    try:
        require_configuration()
    except HTTPException:
        return _failure_redirect("digilocker_not_configured")

    if error or not code:
        detail = (error_description or error or "authorization_denied").strip()
        return _failure_redirect(detail.replace(" ", "_")[:160])

    oauth_state = digilocker_states.find_one_and_delete(
        {"state": state, "expires_at": {"$gt": datetime.now(timezone.utc)}}
    )
    if oauth_state is None:
        # Retry without expiry predicate in case of naive/aware datetime mismatch in older rows.
        oauth_state = digilocker_states.find_one_and_delete({"state": state})
        if oauth_state is None:
            return _failure_redirect("invalid_or_expired_state")
        expires_at = oauth_state.get("expires_at")
        if isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                return _failure_redirect("invalid_or_expired_state")

    shop_id_hint = oauth_state.get("shop_id")
    code_verifier = oauth_state.get("code_verifier")

    try:
        with httpx.Client(timeout=20.0) as client:
            token_payload: dict[str, str] = {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": DIGILOCKER_CLIENT_ID,
                "client_secret": DIGILOCKER_CLIENT_SECRET,
                "redirect_uri": DIGILOCKER_REDIRECT_URI,
            }
            if code_verifier:
                token_payload["code_verifier"] = code_verifier

            token_response = client.post(
                DIGILOCKER_TOKEN_URL,
                data=token_payload,
                headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            )
            if token_response.status_code >= 400:
                # Legacy partners still pointed at /oauth2/1/token — try once if default v2 failed.
                legacy_token_url = "https://digilocker.meripehchaan.gov.in/public/oauth2/1/token"
                if DIGILOCKER_TOKEN_URL.rstrip("/") != legacy_token_url and "/oauth2/2/token" in DIGILOCKER_TOKEN_URL:
                    legacy = client.post(
                        legacy_token_url,
                        data=token_payload,
                        headers={
                            "Content-Type": "application/x-www-form-urlencoded",
                            "Accept": "application/json",
                        },
                    )
                    if legacy.status_code < 400:
                        token_response = legacy
                    else:
                        try:
                            err = token_response.json()
                            desc = err.get("error_description") or err.get("error") or "token_exchange_failed"
                        except ValueError:
                            desc = "token_exchange_failed"
                        return _failure_redirect(str(desc).replace(" ", "_")[:160], shop_id_hint)
                else:
                    try:
                        err = token_response.json()
                        desc = err.get("error_description") or err.get("error") or "token_exchange_failed"
                    except ValueError:
                        desc = "token_exchange_failed"
                    return _failure_redirect(str(desc).replace(" ", "_")[:160], shop_id_hint)

            token_json = token_response.json()
            access_token = token_json.get("access_token")
            if not access_token:
                return _failure_redirect("missing_access_token", shop_id_hint)

            identity: dict = {}
            user_response = client.get(
                DIGILOCKER_USER_URL,
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
            if user_response.status_code < 400:
                try:
                    body = user_response.json()
                    if isinstance(body, dict):
                        identity = body
                except ValueError:
                    identity = {}
            if not identity.get("digilockerid"):
                identity = {**_identity_from_id_token(token_json.get("id_token")), **identity}
    except httpx.HTTPError:
        return _failure_redirect("digilocker_unreachable", shop_id_hint)

    digilocker_id = identity.get("digilockerid")
    if not digilocker_id:
        return _failure_redirect("missing_digilocker_id", shop_id_hint)

    now = datetime.now(timezone.utc)
    digilocker_set = {
        "digilocker_verified": True,
        "digilocker_id": digilocker_id,
        "digilocker_name": identity.get("name"),
        "digilocker_dob": identity.get("dob"),
        "digilocker_gender": identity.get("gender"),
        "digilocker_verified_at": now,
        "updated_at": now,
    }

    try:
        user = users.find_one({"_id": ObjectId(str(oauth_state["user_id"]))})
    except Exception:
        user = None
    if user is None:
        return _failure_redirect("user_missing", shop_id_hint)

    shop_id = oauth_state.get("shop_id")
    if shop_id:
        try:
            shop = shops.find_one({"_id": ObjectId(str(shop_id))})
        except Exception:
            shop = None
        if shop is None:
            return _failure_redirect("shop_missing", shop_id)
        try:
            ensure_shop_access(user, shop)
        except HTTPException:
            return _failure_redirect("shop_access_denied", shop_id)
        shops.update_one({"_id": shop["_id"]}, {"$set": digilocker_set})
    else:
        # Legacy connect without shop_id — keep writing on the user.
        users.update_one({"_id": user["_id"]}, {"$set": digilocker_set})

    success_url = DIGILOCKER_SUCCESS_REDIRECT.strip()
    if success_url:
        if shop_id:
            sep = "&" if "?" in success_url else "?"
            success_url = f"{success_url}{sep}shop_id={shop_id}"
        return RedirectResponse(url=success_url, status_code=302)

    mobile_verified = user.get("mobile_verified", bool(user.get("phone_number")))
    return DigiLockerCallbackResponse(
        message="DigiLocker verified",
        digilocker_verified=True,
        profile_complete=bool(user.get("display_name") and user.get("phone_number") and mobile_verified),
    )
