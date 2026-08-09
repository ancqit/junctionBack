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
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field, field_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .database import otp_requests, users

router = APIRouter(prefix="/auth", tags=["authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
PBKDF2_ITERATIONS = 600_000
OTP_EXPIRE_MINUTES = int(os.getenv("OTP_EXPIRE_MINUTES", "5"))
GCP_SEND_OTP_URL = "https://identitytoolkit.googleapis.com/v1/accounts:sendVerificationCode"
GCP_VERIFY_OTP_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPhoneNumber"


def gcp_identity_platform_api_key() -> str:
    return os.getenv("GCP_IDENTITY_PLATFORM_API_KEY", "").strip()


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


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserSummary


class OtpRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    phone_number: str = Field(pattern=r"^\+[1-9]\d{7,14}$")
    recaptcha_token: str = Field(min_length=1)

    @field_validator("display_name")
    @classmethod
    def display_name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("display_name must not be blank")
        return value


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
    return UserSummary(id=str(document["_id"]), email=document.get("email"), phone_number=document.get("phone_number"), display_name=document["display_name"])


def require_gcp_otp_configuration() -> str:
    api_key = gcp_identity_platform_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="GCP Identity Platform API key is not configured")
    return api_key


def gcp_error(response: httpx.Response) -> HTTPException:
    try:
        message = response.json().get("error", {}).get("message", "OTP provider request failed")
    except ValueError:
        message = "OTP provider request failed"
    return HTTPException(status_code=400, detail=message)


def create_access_token(user_id: ObjectId) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES)}, _secret(), algorithm="HS256")


def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> dict:
    error = HTTPException(status_code=401, detail="Invalid or expired access token", headers={"WWW-Authenticate": "Bearer"})
    try:
        user_id = jwt.decode(token, _secret(), algorithms=["HS256"]).get("sub")
        if not user_id or not ObjectId.is_valid(user_id):
            raise error
    except jwt.PyJWTError:
        raise error
    user = users.find_one({"_id": ObjectId(user_id)})
    if user is None:
        raise error
    return user


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest) -> TokenResponse:
    email = str(payload.email).lower()
    users.create_index("email", unique=True, sparse=True)
    if users.find_one({"email": email}) is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    now = datetime.now(timezone.utc)
    document = {"email": email, "password_hash": hash_password(payload.password), "display_name": payload.display_name, "bio": None, "avatar_url": None, "created_at": now, "updated_at": now}
    try:
        result = users.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    document["_id"] = result.inserted_id
    return TokenResponse(access_token=create_access_token(result.inserted_id), user=user_summary(document))


@router.post("/login", response_model=TokenResponse)
def login(form: Annotated[OAuth2PasswordRequestForm, Depends()]) -> TokenResponse:
    user = users.find_one({"email": form.username.lower()})
    if user is None or not verify_password(form.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password", headers={"WWW-Authenticate": "Bearer"})
    return TokenResponse(access_token=create_access_token(user["_id"]), user=user_summary(user))


@router.post("/otp/request", response_model=OtpRequestResponse)
def request_otp(payload: OtpRequest) -> OtpRequestResponse:
    api_key = require_gcp_otp_configuration()
    try:
        response = httpx.post(
            GCP_SEND_OTP_URL,
            params={"key": api_key},
            json={"phoneNumber": payload.phone_number, "recaptchaToken": payload.recaptcha_token},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to reach GCP Identity Platform") from exc
    if response.is_error:
        raise gcp_error(response)
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
    api_key = require_gcp_otp_configuration()
    now = datetime.now(timezone.utc)
    session_hash = hashlib.sha256(payload.session_info.encode()).hexdigest()
    request = otp_requests.find_one({"phone_number": payload.phone_number, "session_hash": session_hash, "expires_at": {"$gt": now}})
    if request is None:
        raise HTTPException(status_code=401, detail="Invalid or expired OTP session")
    try:
        response = httpx.post(
            GCP_VERIFY_OTP_URL,
            params={"key": api_key},
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
    user = users.find_one({"phone_number": payload.phone_number})
    if user is None:
        document = {"phone_number": payload.phone_number, "mobile_verified": True, "gcp_identity_id": gcp_user_id, "display_name": request["display_name"], "bio": None, "avatar_url": None, "created_at": now, "updated_at": now}
        try:
            result = users.insert_one(document)
            document["_id"] = result.inserted_id
            user = document
        except DuplicateKeyError:
            user = users.find_one({"phone_number": payload.phone_number})
    user = users.find_one_and_update(
        {"_id": user["_id"]},
        {"$set": {"mobile_verified": True, "gcp_identity_id": gcp_user_id, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    return TokenResponse(access_token=create_access_token(user["_id"]), user=user_summary(user))
