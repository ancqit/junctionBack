"""Short-lived guest sessions for junction.today (no user login)."""

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .database import sessions
from .login import JWT_SECRET, oauth2_scheme
from .rate_limit import RATE_LIMIT_AUTH, limiter

router = APIRouter(prefix="/session", tags=["session"])

SESSION_EXPIRE_SECONDS = int(os.getenv("SESSION_EXPIRE_SECONDS", "100"))
SESSION_AUDIENCE = "junction.today"
SESSION_TOKEN_TYPE = "junction_session"


class SessionResponse(BaseModel):
    session_id: str
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Seconds until the session JWT expires")
    audience: str = SESSION_AUDIENCE


def _secret() -> str:
    if len(JWT_SECRET) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT_SECRET must contain at least 32 characters",
        )
    return JWT_SECRET


def _ensure_session_ttl_index() -> None:
    sessions.create_index("session_id", unique=True)
    sessions.create_index("expires_at", expireAfterSeconds=0)


def create_session_access_token(session_id: str, *, expires_at: datetime) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": session_id,
            "typ": SESSION_TOKEN_TYPE,
            "aud": SESSION_AUDIENCE,
            "iat": now,
            "exp": expires_at,
        },
        _secret(),
        algorithm="HS256",
    )


def require_junction_session(token: Annotated[str, Depends(oauth2_scheme)]) -> dict:
    """Validate a short-lived junction.today session JWT."""
    try:
        payload = jwt.decode(
            token,
            _secret(),
            algorithms=["HS256"],
            audience=SESSION_AUDIENCE,
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if payload.get("typ") != SESSION_TOKEN_TYPE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    session_id = payload.get("sub")
    if not session_id or not isinstance(session_id, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    document = sessions.find_one({"session_id": session_id})
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session not found or expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    expires_at = document.get("expires_at")
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            sessions.delete_one({"session_id": session_id})
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return {
        "session_id": session_id,
        "audience": payload.get("aud", SESSION_AUDIENCE),
        "expires_at": expires_at,
    }


JunctionSession = Annotated[dict, Depends(require_junction_session)]


def require_catalog_reader(token: Annotated[str, Depends(oauth2_scheme)]) -> dict:
    """
    Accept either a junction.today session JWT or a normal user JWT.
    Used for catalog reads (shops/products) shared by the owner app and junction.today.
    """
    try:
        payload = jwt.decode(
            token,
            _secret(),
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if payload.get("typ") == SESSION_TOKEN_TYPE:
        session = require_junction_session(token)
        return {"kind": "session", "session": session, "user": None}

    from .login import get_current_user

    user = get_current_user(token)
    return {"kind": "user", "session": None, "user": user}


CatalogReader = Annotated[dict, Depends(require_catalog_reader)]


def is_junction_session(auth: dict) -> bool:
    return auth.get("kind") == "session"


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_AUTH)
def create_session(request: Request) -> SessionResponse:
    """
    Issue a guest session for junction.today.
    Returns session_id + JWT valid for SESSION_EXPIRE_SECONDS (default 100).
    """
    _ensure_session_ttl_index()
    now = datetime.now(timezone.utc)
    expires_in = max(SESSION_EXPIRE_SECONDS, 1)
    expires_at = now + timedelta(seconds=expires_in)
    session_id = secrets.token_urlsafe(24)

    sessions.insert_one(
        {
            "session_id": session_id,
            "audience": SESSION_AUDIENCE,
            "created_at": now,
            "expires_at": expires_at,
        }
    )

    access_token = create_session_access_token(session_id, expires_at=expires_at)
    return SessionResponse(
        session_id=session_id,
        access_token=access_token,
        expires_in=expires_in,
        audience=SESSION_AUDIENCE,
    )
