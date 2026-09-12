import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated
from urllib.parse import urlencode

import httpx
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .database import digilocker_states, shops, users
from .login import get_current_user
from .access_control import get_shop_by_store_id, ensure_shop_access


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
DIGILOCKER_TOKEN_URL = os.getenv(
    "DIGILOCKER_TOKEN_URL",
    "https://digilocker.meripehchaan.gov.in/public/oauth2/1/token",
)
DIGILOCKER_USER_URL = os.getenv(
    "DIGILOCKER_USER_URL",
    "https://digilocker.meripehchaan.gov.in/public/oauth2/1/user",
)
STATE_EXPIRE_MINUTES = 10


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
    now = datetime.now(timezone.utc)
    digilocker_states.create_index("expires_at", expireAfterSeconds=0)
    digilocker_states.insert_one(
        {
            "state": state,
            "user_id": current_user["_id"],
            "shop_id": resolved_shop_id,
            "created_at": now,
            "expires_at": now + timedelta(minutes=STATE_EXPIRE_MINUTES),
        }
    )
    query = urlencode(
        {
            "response_type": "code",
            "client_id": DIGILOCKER_CLIENT_ID,
            "redirect_uri": DIGILOCKER_REDIRECT_URI,
            "state": state,
            "scope": "openid",
        }
    )
    return DigiLockerConnectResponse(authorization_url=f"{DIGILOCKER_AUTHORIZE_URL}?{query}")


@router.get("/callback")
def digilocker_callback(
    state: str = Query(min_length=20, max_length=200),
    code: str | None = None,
    error: str | None = None,
):
    require_configuration()
    if error or not code:
        raise HTTPException(status_code=400, detail=error or "DigiLocker authorization code is missing")

    oauth_state = digilocker_states.find_one_and_delete(
        {"state": state, "expires_at": {"$gt": datetime.now(timezone.utc)}}
    )
    if oauth_state is None:
        raise HTTPException(status_code=400, detail="Invalid or expired DigiLocker state")

    try:
        with httpx.Client(timeout=15.0) as client:
            token_response = client.post(
                DIGILOCKER_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": DIGILOCKER_CLIENT_ID,
                    "client_secret": DIGILOCKER_CLIENT_SECRET,
                    "redirect_uri": DIGILOCKER_REDIRECT_URI,
                },
            )
            token_response.raise_for_status()
            access_token = token_response.json().get("access_token")
            if not access_token:
                raise ValueError("DigiLocker did not return an access token")
            user_response = client.get(
                DIGILOCKER_USER_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_response.raise_for_status()
            identity = user_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="DigiLocker verification failed") from exc

    digilocker_id = identity.get("digilockerid")
    if not digilocker_id:
        raise HTTPException(status_code=502, detail="DigiLocker user identity was not returned")

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

    user = users.find_one({"_id": ObjectId(oauth_state["user_id"])})
    if user is None:
        raise HTTPException(status_code=404, detail="User no longer exists")

    shop_id = oauth_state.get("shop_id")
    if shop_id:
        shop = shops.find_one({"_id": ObjectId(str(shop_id))})
        if shop is None:
            raise HTTPException(status_code=404, detail="Shop no longer exists")
        ensure_shop_access(user, shop)
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
