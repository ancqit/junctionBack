"""Free GSTIN verification via the public GST portal taxpayer search.

Same approach as https://github.com/shubham-dube/GST-Verification-API:
fetch captcha + cookies from services.gst.gov.in, user solves captcha,
then POST taxpayerDetails. No paid KYC vendor required.

Note: this depends on the public portal; treat as best-effort identity
for shop owners, not a licensed GST Suvidha Provider feed.
(API Setu DigiLocker is separate — see app/digilocker.py. Paid Setu.co
GST KYC is intentionally not used here.)
"""

import base64
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from .access_control import ensure_shop_access, get_shop_by_store_id
from .database import gst_sessions, shops, users
from .login import get_current_user
from .rate_limit import RATE_LIMIT_AUTH, limiter

router = APIRouter(prefix="/gst", tags=["gst"])

GST_SEARCH_PAGE = "https://services.gst.gov.in/services/searchtp"
GST_CAPTCHA_URL = "https://services.gst.gov.in/services/captcha"
GST_TAXPAYER_URL = "https://services.gst.gov.in/services/api/search/taxpayerDetails"
SESSION_TTL_MINUTES = 10
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$")

_GST_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class GstCaptchaResponse(BaseModel):
    session_id: str
    image: str


class GstVerifyRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=80)
    gstin: str = Field(min_length=15, max_length=15)
    captcha: str = Field(min_length=1, max_length=12)
    shop_id: str | None = Field(default=None, max_length=80)
    store_id: str | None = Field(default=None, max_length=80, description="Alias of shop_id")

    @field_validator("gstin")
    @classmethod
    def normalize_gstin(cls, value: str) -> str:
        gstin = value.strip().upper()
        if not _GSTIN_RE.fullmatch(gstin):
            raise ValueError("Invalid GSTIN format")
        return gstin

    @field_validator("captcha")
    @classmethod
    def trim_captcha(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("captcha is required")
        return trimmed

    @field_validator("shop_id", "store_id")
    @classmethod
    def strip_optional_ids(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class GstVerifyResponse(BaseModel):
    gstin: str
    gst_verified: bool
    legal_name: str | None = None
    trade_name: str | None = None
    status: str | None = None
    taxpayer_type: str | None = None
    message: str
    shop_id: str | None = None


def _cookie_header_from_list(items: list[dict]) -> str:
    parts: list[str] = []
    for item in items:
        name = item.get("name")
        value = item.get("value")
        if name and value is not None:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def _cookies_to_list(cookies: httpx.Cookies) -> list[dict]:
    return [
        {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
        }
        for cookie in cookies.jar
    ]


def _portal_error_detail(data: dict) -> str | None:
    error_code = (data.get("errorCode") or data.get("error_code") or "").strip()
    message = data.get("error") or data.get("message")
    if isinstance(message, str):
        message = message.strip() or None
    else:
        message = None

    if error_code in {"SWEB_9000", "SWEB9000"}:
        return "Invalid or expired captcha. Refresh the captcha and try again."
    if error_code:
        return message or f"GST portal rejected the request ({error_code})."
    return message


@router.get("/captcha", response_model=GstCaptchaResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def get_gst_captcha(request: Request) -> GstCaptchaResponse:
    """Start a free GST portal captcha session for profile verification."""
    try:
        with httpx.Client(timeout=25.0, follow_redirects=True, headers=_GST_BROWSER_HEADERS) as client:
            page = client.get(
                GST_SEARCH_PAGE,
                headers={
                    **_GST_BROWSER_HEADERS,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            page.raise_for_status()
            captcha_response = client.get(
                GST_CAPTCHA_URL,
                headers={
                    **_GST_BROWSER_HEADERS,
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    "Referer": GST_SEARCH_PAGE,
                },
            )
            captcha_response.raise_for_status()
            cookies = _cookies_to_list(client.cookies)
            cookie_header = _cookie_header_from_list(cookies)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to reach GST portal for captcha") from exc

    content_type = (captcha_response.headers.get("content-type") or "").lower()
    if not captcha_response.content or (
        "image" not in content_type and not captcha_response.content.startswith(b"\x89PNG")
    ):
        raise HTTPException(status_code=502, detail="GST portal returned an empty or invalid captcha")

    session_id = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    gst_sessions.create_index("expires_at", expireAfterSeconds=0)
    gst_sessions.insert_one(
        {
            "session_id": session_id,
            "cookies": cookies,
            "cookie_header": cookie_header,
            "created_at": now,
            "expires_at": now + timedelta(minutes=SESSION_TTL_MINUTES),
        }
    )

    image = "data:image/png;base64," + base64.b64encode(captcha_response.content).decode("ascii")
    return GstCaptchaResponse(session_id=session_id, image=image)


@router.post("/verify", response_model=GstVerifyResponse, status_code=status.HTTP_200_OK)
@limiter.limit(RATE_LIMIT_AUTH)
def verify_gstin(
    request: Request,
    payload: GstVerifyRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> GstVerifyResponse:
    """Verify GSTIN against the public GST portal using the captcha session."""
    stored = gst_sessions.find_one({"session_id": payload.session_id})
    if stored is None:
        raise HTTPException(status_code=400, detail="Captcha session expired. Refresh captcha and try again.")

    cookie_header = stored.get("cookie_header") or _cookie_header_from_list(stored.get("cookies") or [])
    if not cookie_header:
        gst_sessions.delete_one({"session_id": payload.session_id})
        raise HTTPException(status_code=400, detail="Captcha session is invalid. Refresh captcha and try again.")

    # Consume after we know the session exists — portal soft-failures still need a fresh captcha.
    gst_sessions.delete_one({"session_id": payload.session_id})

    try:
        with httpx.Client(timeout=25.0, follow_redirects=True, headers=_GST_BROWSER_HEADERS) as client:
            # Re-warm the search page with the same cookie jar (F5 / portal session affinity).
            client.get(
                GST_SEARCH_PAGE,
                headers={
                    **_GST_BROWSER_HEADERS,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Cookie": cookie_header,
                    "Referer": GST_SEARCH_PAGE,
                },
            )
            response = client.post(
                GST_TAXPAYER_URL,
                json={"gstin": payload.gstin, "captcha": payload.captcha},
                headers={
                    **_GST_BROWSER_HEADERS,
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json;charset=UTF-8",
                    "Origin": "https://services.gst.gov.in",
                    "Referer": GST_SEARCH_PAGE,
                    "X-Requested-With": "XMLHttpRequest",
                    "Cookie": cookie_header,
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to reach GST portal") from exc

    if response.status_code >= 400:
        raise HTTPException(status_code=400, detail="GST portal rejected the request. Check captcha/GSTIN.")

    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="GST portal returned an invalid response") from exc

    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Unexpected GST portal response")

    portal_error = _portal_error_detail(data)
    legal_name = (data.get("lgnm") or data.get("legalName") or "").strip() or None
    trade_name = (data.get("tradeNam") or data.get("tradeName") or "").strip() or None
    gst_status = (data.get("sts") or data.get("status") or "").strip() or None
    taxpayer_type = (data.get("dty") or data.get("taxpayerType") or "").strip() or None
    returned_gstin = (data.get("gstin") or payload.gstin).strip().upper()

    if not legal_name and portal_error:
        raise HTTPException(status_code=400, detail=portal_error)
    if not legal_name:
        raise HTTPException(
            status_code=400,
            detail="Could not verify GSTIN. Captcha may be wrong, or GSTIN was not found.",
        )

    now = datetime.now(timezone.utc)
    gst_set = {
        "gstin": returned_gstin,
        "gst_verified": True,
        "gst_legal_name": legal_name,
        "gst_trade_name": trade_name,
        "gst_status": gst_status,
        "gst_taxpayer_type": taxpayer_type,
        "gst_verified_at": now,
        "updated_at": now,
    }

    resolved_shop_id = payload.shop_id or payload.store_id
    shop_id_out: str | None = None
    if resolved_shop_id:
        shop = get_shop_by_store_id(resolved_shop_id)
        ensure_shop_access(current_user, shop)
        shops.update_one({"_id": shop["_id"]}, {"$set": gst_set})
        shop_id_out = str(shop["_id"])
    else:
        # Legacy: no shop_id → keep writing on the user.
        users.update_one({"_id": current_user["_id"]}, {"$set": gst_set})

    return GstVerifyResponse(
        gstin=returned_gstin,
        gst_verified=True,
        legal_name=legal_name,
        trade_name=trade_name,
        status=gst_status,
        taxpayer_type=taxpayer_type,
        message="GSTIN verified from the public GST portal",
        shop_id=shop_id_out,
    )
