import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
import httpx
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .admin_registry import is_admin_user
from .database import otp_requests, users
from .plan_service import PlanSummary, build_plan_summary, initialize_user_plan, resolve_login_plan_string, restore_persisted_plan
from .rate_limit import RATE_LIMIT_AUTH, limiter
from .role_keeper import resolve_role_from_keeper
from .roles import UserRole, get_user_role

router = APIRouter(prefix="/auth", tags=["authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
PBKDF2_ITERATIONS = 600_000
OTP_EXPIRE_MINUTES = int(os.getenv("OTP_EXPIRE_MINUTES", "5"))
GCP_IDENTITY_PLATFORM_API_KEY = os.getenv("GCP_IDENTITY_PLATFORM_API_KEY", "")
GCP_SEND_OTP_URL = "https://identitytoolkit.googleapis.com/v1/accounts:sendVerificationCode"
GCP_VERIFY_OTP_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPhoneNumber"


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=100)

    @field_validator("display_name")
    @classmethod
    def display_name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("display_name must not be blank")
        return value


class UserSummary(BaseModel):
    id: str
    email: EmailStr | None = None
    phone_number: str | None = None
    display_name: str
    role: UserRole
    plan: str = ""


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserSummary
    plan: PlanSummary
    role: UserRole


class RoleInfo(BaseModel):
    value: UserRole
    label: str
    description: str


class AuthMeResponse(BaseModel):
    user: UserSummary
    role: UserRole
    plan: PlanSummary


AVAILABLE_ROLES = [
    RoleInfo(value=UserRole.admin, label="Admin", description="Platform administrator with full user management access"),
    RoleInfo(value=UserRole.owner, label="Owner", description="Store owner with full business access"),
    RoleInfo(value=UserRole.viewer, label="Viewer", description="Read-only access to business data"),
]


class OtpRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    phone_number: str = Field(pattern=r"^\+[1-9]\d{7,14}$")
    recaptcha_token: str | None = None
    play_integrity_token: str | None = None
    client_type: str | None = None

    @field_validator("display_name")
    @classmethod
    def display_name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("display_name must not be blank")
        return value

    @model_validator(mode="after")
    def verification_token_present(self) -> "OtpRequest":
        if not self.recaptcha_token and not self.play_integrity_token:
            raise ValueError("recaptcha_token or play_integrity_token is required")
        return self


class OtpRequestResponse(BaseModel):
    message: str
    expires_in_seconds: int
    session_info: str


class OtpVerifyRequest(BaseModel):
    phone_number: str = Field(pattern=r"^\+[1-9]\d{7,14}$")
    otp: str = Field(pattern=r"^\d{6}$")
    session_info: str = Field(min_length=1)


def _secret() -> str:
    if len(JWT_SECRET) < 32:
        raise HTTPException(status_code=503, detail="JWT_SECRET must contain at least 32 characters")
    return JWT_SECRET


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(base64.b64encode(digest).decode(), expected)
    except (ValueError, TypeError):
        return False


def user_summary(document: dict) -> UserSummary:
    return UserSummary(
        id=str(document["_id"]),
        email=document.get("email"),
        phone_number=document.get("phone_number"),
        display_name=document["display_name"],
        role=get_user_role(document),
        plan=resolve_login_plan_string(document),
    )


def resolve_role_for_user(email: str | None = None, phone_number: str | None = None) -> str:
    return resolve_role_from_keeper(email=email, phone_number=phone_number)


def sync_role_from_keeper(user: dict) -> dict:
    """Resolve role: admin list always wins; viewers only after trial/grace expiry."""
    now = datetime.now(timezone.utc)
    if is_admin_user(email=user.get("email"), phone_number=user.get("phone_number")):
        target_role = UserRole.admin.value
    elif (user.get("plan") or {}).get("viewing_applied"):
        target_role = UserRole.viewer.value
    else:
        target_role = UserRole.owner.value

    update_doc: dict = {}
    if user.get("role") != target_role:
        update_doc["$set"] = {"role": target_role, "updated_at": now}
    if user.get("account_status") or user.get("pre_deactivation_role"):
        update_doc.setdefault("$unset", {})
        update_doc["$unset"]["account_status"] = ""
        update_doc["$unset"]["pre_deactivation_role"] = ""

    if not update_doc:
        return user

    updated = users.find_one_and_update(
        {"_id": user["_id"]},
        update_doc,
        return_document=ReturnDocument.AFTER,
    )
    return updated or user


def require_gcp_otp_configuration() -> None:
    if not GCP_IDENTITY_PLATFORM_API_KEY:
        raise HTTPException(status_code=503, detail="GCP Identity Platform API key is not configured")


def gcp_error(response: httpx.Response, *, client_type: str | None = None) -> HTTPException:
    try:
        message = response.json().get("error", {}).get("message", "OTP provider request failed")
    except ValueError:
        message = "OTP provider request failed"
    upper = message.upper()
    hint = (client_type or "").strip().lower()
    if "TOO_MANY_ATTEMPTS" in upper:
        message = "Too many OTP attempts. Wait a few minutes and try again."
    elif "NONCE" in upper or "BASE64" in upper:
        message = (
            "Play Integrity nonce is invalid. "
            "Android must send SHA-256(E.164 phone) as Base64 URL-safe no-wrap (no padding)."
        )
    elif "CAPTCHA" in upper or "RECAPTCHA" in upper:
        if hint in {"android", "client_type_android"}:
            message = (
                "Android bot check failed. Send play_integrity_token "
                "(not a web reCAPTCHA token) with client_type=android."
            )
        else:
            message = (
                "Web bot check failed. Refresh and complete reCAPTCHA, then retry. "
                "Web must send recaptcha_token with client_type=web (not an integrity token)."
            )
    elif "INVALID_PHONE" in upper or "PHONE_NUMBER" in upper:
        message = "Invalid phone number. Use E.164 format, e.g. +9198XXXXXXXX."
    elif "QUOTA" in upper or "BILLING" in upper:
        message = "SMS quota or billing issue on Identity Platform. Check the GCP/Firebase console."
    return HTTPException(status_code=400, detail=message)


def gcp_send_otp_payload(payload: OtpRequest) -> dict:
    """
    Strict dual flows (do not mix tokens):
    - Web: recaptcha_token + CLIENT_TYPE_WEB (+ RECAPTCHA_VERSION_2)
    - Android APK: play_integrity_token + CLIENT_TYPE_ANDROID
      (nonce inside token = Base64 URL-safe no-wrap SHA-256 of E.164 phone)
    """
    phone = payload.phone_number.strip()
    body: dict = {"phoneNumber": phone}
    hint = (payload.client_type or "").strip().lower()
    recaptcha = (payload.recaptcha_token or "").strip() or None
    play_integrity = (payload.play_integrity_token or "").strip() or None

    if hint in {"android", "client_type_android"}:
        if not play_integrity:
            raise HTTPException(
                status_code=400,
                detail="Android clients must send play_integrity_token (not recaptcha_token).",
            )
        body["playIntegrityToken"] = play_integrity
        body["clientType"] = "CLIENT_TYPE_ANDROID"
        return body

    if hint in {"web", "client_type_web"}:
        if not recaptcha:
            raise HTTPException(
                status_code=400,
                detail="Web clients must send recaptcha_token (not play_integrity_token).",
            )
        body["recaptchaToken"] = recaptcha
        body["clientType"] = "CLIENT_TYPE_WEB"
        body["recaptchaVersion"] = "RECAPTCHA_VERSION_2"
        return body

    # Legacy clients without client_type: prefer explicit token present.
    if play_integrity and not recaptcha:
        body["playIntegrityToken"] = play_integrity
        body["clientType"] = "CLIENT_TYPE_ANDROID"
        return body
    if recaptcha and not play_integrity:
        body["recaptchaToken"] = recaptcha
        body["clientType"] = "CLIENT_TYPE_WEB"
        body["recaptchaVersion"] = "RECAPTCHA_VERSION_2"
        return body
    if play_integrity and recaptcha:
        raise HTTPException(
            status_code=400,
            detail=(
                "Send only one verification token. "
                "Web: recaptcha_token + client_type=web. "
                "Android: play_integrity_token + client_type=android."
            ),
        )
    raise HTTPException(
        status_code=400,
        detail="recaptcha_token or play_integrity_token is required",
    )

class RecaptchaParamsResponse(BaseModel):
    recaptcha_site_key: str


def token_response(user: dict | None) -> TokenResponse:
    if user is None:
        raise HTTPException(status_code=500, detail="Could not load user after authentication")
    role = get_user_role(user)
    if role != UserRole.admin:
        if user.get("plan") is None:
            initialize_user_plan(user["_id"])
            refreshed = users.find_one({"_id": user["_id"]})
            if refreshed is not None:
                user = refreshed
        user = restore_persisted_plan(user)
    user = sync_role_from_keeper(user)
    role = get_user_role(user)
    try:
        plan = build_plan_summary(user)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not build plan for this account: {exc}",
        ) from exc
    return TokenResponse(
        access_token=create_access_token(user["_id"]),
        user=user_summary(user),
        plan=plan,
        role=role,
    )


def create_access_token(user_id: ObjectId) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES)}, _secret(), algorithm="HS256")


def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> dict:
    error = HTTPException(status_code=401, detail="Invalid or expired access token", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, _secret(), algorithms=["HS256"])
        if payload.get("typ") == "junction_session":
            raise HTTPException(
                status_code=401,
                detail="User access token required (session tokens cannot access this route)",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user_id = payload.get("sub")
        if not user_id or not ObjectId.is_valid(user_id):
            raise error
    except jwt.PyJWTError:
        raise error
    user = users.find_one({"_id": ObjectId(user_id)})
    if user is None:
        raise error
    user = restore_persisted_plan(user)
    return sync_role_from_keeper(user)


@router.get("/roles", response_model=list[RoleInfo])
def list_roles() -> list[RoleInfo]:
    return AVAILABLE_ROLES


@router.get("/recaptcha-params", response_model=RecaptchaParamsResponse)
def get_recaptcha_params() -> RecaptchaParamsResponse:
    """
    Public helper for the web login flow.
    Returns the Identity Platform reCAPTCHA site key used by invisible reCAPTCHA.
    """
    require_gcp_otp_configuration()
    try:
        response = httpx.get(
            "https://identitytoolkit.googleapis.com/v1/recaptchaParams",
            params={"key": GCP_IDENTITY_PLATFORM_API_KEY},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to reach GCP Identity Platform") from exc
    if response.is_error:
        raise gcp_error(response, client_type="web")
    site_key = response.json().get("recaptchaSiteKey")
    if not site_key:
        raise HTTPException(status_code=502, detail="GCP did not return a reCAPTCHA site key")
    return RecaptchaParamsResponse(recaptcha_site_key=site_key)


@router.get("/me", response_model=AuthMeResponse)
def read_current_session(current_user: Annotated[dict, Depends(get_current_user)]) -> AuthMeResponse:
    role = get_user_role(current_user)
    return AuthMeResponse(
        user=user_summary(current_user),
        role=role,
        plan=build_plan_summary(current_user),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_AUTH)
def register(request: Request, payload: RegisterRequest) -> TokenResponse:
    email = str(payload.email).lower()
    users.create_index("email", unique=True, sparse=True)
    if users.find_one({"email": email}) is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    now = datetime.now(timezone.utc)
    document = {
        "email": email,
        "password_hash": hash_password(payload.password),
        "display_name": payload.display_name,
        "role": resolve_role_for_user(email=email),
        "account_status": "active",
        "bio": None,
        "avatar_url": None,
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = users.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    document["_id"] = result.inserted_id
    if resolve_role_for_user(email=email) != UserRole.admin.value:
        initialize_user_plan(result.inserted_id)
    document = users.find_one({"_id": result.inserted_id})
    return token_response(document)


@router.post("/login", response_model=TokenResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def login(request: Request, form: Annotated[OAuth2PasswordRequestForm, Depends()]) -> TokenResponse:
    user = users.find_one({"email": form.username.lower()})
    if user is None or not verify_password(form.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password", headers={"WWW-Authenticate": "Bearer"})
    return token_response(user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(token: Annotated[str, Depends(oauth2_scheme)]) -> TokenResponse:
    user = get_current_user(token)
    return token_response(user)


@router.post("/otp/request", response_model=OtpRequestResponse)
@limiter.limit(RATE_LIMIT_AUTH)
def request_otp(request: Request, payload: OtpRequest) -> OtpRequestResponse:
    require_gcp_otp_configuration()
    try:
        response = httpx.post(
            GCP_SEND_OTP_URL,
            params={"key": GCP_IDENTITY_PLATFORM_API_KEY},
            json=gcp_send_otp_payload(payload),
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to reach GCP Identity Platform") from exc
    if response.is_error:
        raise gcp_error(response, client_type=payload.client_type)
    session_info = response.json().get("sessionInfo")
    if not session_info:
        raise HTTPException(status_code=502, detail="GCP did not return an OTP session")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=OTP_EXPIRE_MINUTES)
    otp_requests.create_index("expires_at", expireAfterSeconds=0)
    otp_requests.update_one(
        {"phone_number": payload.phone_number},
        {"$set": {"display_name": payload.display_name, "session_hash": hashlib.sha256(session_info.encode()).hexdigest(), "created_at": now, "expires_at": expires_at}},
        upsert=True,
    )
    return OtpRequestResponse(message="OTP sent by GCP Identity Platform", expires_in_seconds=OTP_EXPIRE_MINUTES * 60, session_info=session_info)


@router.post("/otp/verify", response_model=TokenResponse)
def verify_otp(payload: OtpVerifyRequest) -> TokenResponse:
    require_gcp_otp_configuration()
    now = datetime.now(timezone.utc)
    session_hash = hashlib.sha256(payload.session_info.encode()).hexdigest()
    request = otp_requests.find_one({"phone_number": payload.phone_number, "session_hash": session_hash, "expires_at": {"$gt": now}})
    if request is None:
        raise HTTPException(status_code=401, detail="Invalid or expired OTP session")
    try:
        response = httpx.post(
            GCP_VERIFY_OTP_URL,
            params={"key": GCP_IDENTITY_PLATFORM_API_KEY},
            json={"sessionInfo": payload.session_info, "code": payload.otp},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to reach GCP Identity Platform") from exc
    if response.is_error:
        raise gcp_error(response)
    verified_phone = response.json().get("phoneNumber")
    gcp_user_id = response.json().get("localId")
    if verified_phone != payload.phone_number or not gcp_user_id:
        raise HTTPException(status_code=401, detail="GCP phone verification did not match the request")

    otp_requests.delete_one({"_id": request["_id"]})
    users.create_index("phone_number", unique=True, sparse=True)
    display_name = request["display_name"].strip()
    user = users.find_one({"phone_number": payload.phone_number})
    if user is None:
        document = {
            "phone_number": payload.phone_number,
            "mobile_verified": True,
            "gcp_identity_id": gcp_user_id,
            "display_name": display_name,
            "role": resolve_role_for_user(phone_number=payload.phone_number),
            "account_status": "active",
            "bio": None,
            "avatar_url": None,
            "created_at": now,
            "updated_at": now,
        }
        try:
            result = users.insert_one(document)
            if resolve_role_for_user(phone_number=payload.phone_number) != UserRole.admin.value:
                initialize_user_plan(result.inserted_id)
            user = users.find_one({"_id": result.inserted_id})
        except DuplicateKeyError:
            user = users.find_one({"phone_number": payload.phone_number})
    if user is None:
        raise HTTPException(status_code=500, detail="Could not create or load user after OTP verify")
    user = users.find_one_and_update(
        {"_id": user["_id"]},
        {"$set": {"mobile_verified": True, "gcp_identity_id": gcp_user_id, "display_name": display_name, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    return token_response(user)
