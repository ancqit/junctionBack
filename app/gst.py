"""Free GSTIN verification via the public GST portal taxpayer search.

Same approach as https://github.com/shubham-dube/GST-Verification-API:
fetch captcha + cookies from services.gst.gov.in, user solves captcha,
then POST taxpayerDetails. No paid KYC vendor required.

Note: this depends on the public portal; treat as best-effort identity
for shop owners, not a licensed GST Suvidha Provider feed.
"""

import base64
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from .database import gst_sessions, users
from .login import get_current_user
from .rate_limit import RATE_LIMIT_AUTH, limiter

router = APIRouter(prefix="/gst", tags=["gst"])

GST_SEARCH_PAGE = "https://services.gst.gov.in/services/searchtp"
GST_CAPTCHA_URL = "https://services.gst.gov.in/services/captcha"
GST_TAXPAYER_URL = "https://services.gst.gov.in/services/api/search/taxpayerDetails"
SESSION_TTL_MINUTES = 10
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$")


class GstCaptchaResponse(BaseModel):
    session_id: str
    image: str


class GstVerifyRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=80)
    gstin: str = Field(min_length=15, max_length=15)
    captcha: str = Field(min_length=1, max_length=12)

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


class GstVerifyResponse(BaseModel):
    gstin: str
    gst_verified: bool
    legal_name: str | None = None
    trade_name: str | None = None
    status: str | None = None
    taxpayer_type: str | None = None
    message: str


def _cookie_jar_from_list(items: list[dict]) -> httpx.Cookies:
    cookies = httpx.Cookies()
    for item in items:
        cookies.set(
            item["name"],
            item["value"],
            domain=item.get("domain"),
            path=item.get("path") or "/",
        )
    return cookies


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


@router.get("/captcha", response_model=GstCaptchaResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def get_gst_captcha(request: Request) -> GstCaptchaResponse:
    """Start a free GST portal captcha session for profile verification."""
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            client.get(GST_SEARCH_PAGE)
            captcha_response = client.get(GST_CAPTCHA_URL)
            captcha_response.raise_for_status()
            cookies = _cookies_to_list(client.cookies)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to reach GST portal for captcha") from exc

    if not captcha_response.content:
        raise HTTPException(status_code=502, detail="GST portal returned an empty captcha")

    session_id = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    gst_sessions.create_index("expires_at", expireAfterSeconds=0)
    gst_sessions.insert_one(
        {
            "session_id": session_id,
            "cookies": cookies,
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
    stored = gst_sessions.find_one_and_delete({"session_id": payload.session_id})
    if stored is None:
        raise HTTPException(status_code=400, detail="Captcha session expired. Refresh captcha and try again.")

    cookies = _cookie_jar_from_list(stored.get("cookies") or [])
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True, cookies=cookies) as client:
            response = client.post(
                GST_TAXPAYER_URL,
                json={"gstin": payload.gstin, "captcha": payload.captcha},
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

    error_message = data.get("error") or data.get("message")
    legal_name = (data.get("lgnm") or data.get("legalName") or "").strip() or None
    trade_name = (data.get("tradeNam") or data.get("tradeName") or "").strip() or None
    gst_status = (data.get("sts") or data.get("status") or "").strip() or None
    taxpayer_type = (data.get("dty") or data.get("taxpayerType") or "").strip() or None
    returned_gstin = (data.get("gstin") or payload.gstin).strip().upper()

    if not legal_name and error_message:
        raise HTTPException(status_code=400, detail=str(error_message))
    if not legal_name:
        raise HTTPException(
            status_code=400,
            detail="Could not verify GSTIN. Captcha may be wrong, or GSTIN was not found.",
        )

    now = datetime.now(timezone.utc)
    users.update_one(
        {"_id": current_user["_id"]},
        {
            "$set": {
                "gstin": returned_gstin,
                "gst_verified": True,
                "gst_legal_name": legal_name,
                "gst_trade_name": trade_name,
                "gst_status": gst_status,
                "gst_taxpayer_type": taxpayer_type,
                "gst_verified_at": now,
                "updated_at": now,
            }
        },
    )

    return GstVerifyResponse(
        gstin=returned_gstin,
        gst_verified=True,
        legal_name=legal_name,
        trade_name=trade_name,
        status=gst_status,
        taxpayer_type=taxpayer_type,
        message="GSTIN verified from the public GST portal",
    )
