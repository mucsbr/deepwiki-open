"""
GitLab OAuth2 SSO Authentication Module

Provides:
- /auth/gitlab/login   → redirect to GitLab OAuth authorize page
- /auth/gitlab/callback → exchange code for token, issue JWT
- /auth/me             → validate JWT, return user info
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from api.config import (
    ADMIN_USERNAMES,
    GITLAB_CLIENT_ID,
    GITLAB_CLIENT_SECRET,
    GITLAB_URL,
    JWT_SECRET_KEY,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# Encryption helpers – Fernet key derived from JWT_SECRET_KEY
# ---------------------------------------------------------------------------

_fernet_key: bytes | None = None


def _get_fernet() -> Fernet:
    global _fernet_key
    if _fernet_key is None:
        import base64
        import hashlib

        # Derive a valid 32-byte Fernet key from JWT_SECRET_KEY
        digest = hashlib.sha256(JWT_SECRET_KEY.encode()).digest()
        _fernet_key = base64.urlsafe_b64encode(digest)
    return Fernet(_fernet_key)


def _encrypt_token(token: str) -> str:
    return _get_fernet().encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    return _get_fernet().decrypt(encrypted.encode()).decode()


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 8

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/gitlab/login", auto_error=False)


def create_jwt(payload: dict) -> str:
    to_encode = payload.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode["exp"] = expire
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=ALGORITHM)


def decode_jwt(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])


# ---------------------------------------------------------------------------
# FastAPI dependency – extract current user from JWT
# ---------------------------------------------------------------------------


async def get_current_user(token: str | None = Depends(oauth2_scheme)) -> dict:
    """Decode JWT and return user payload. Raises 401 if invalid."""
    if token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_jwt(token)
        # Decrypt the GitLab access token stored inside the JWT
        if "gitlab_access_token" in payload:
            payload["gitlab_access_token"] = decrypt_token(payload["gitlab_access_token"])
        return payload
    except JWTError as exc:
        logger.warning("JWT validation failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid or expired token")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")


@router.get("/gitlab/login")
async def gitlab_login():
    """Redirect the user to GitLab's OAuth2 authorization page."""
    if not GITLAB_URL or not GITLAB_CLIENT_ID:
        raise HTTPException(status_code=500, detail="GitLab OAuth not configured")

    params = urlencode(
        {
            "client_id": GITLAB_CLIENT_ID,
            "redirect_uri": f"{FRONTEND_ORIGIN}/auth/gitlab/callback",
            "response_type": "code",
            "scope": "read_user read_api",
        }
    )
    authorize_url = f"{GITLAB_URL}/oauth/authorize?{params}"
    return RedirectResponse(url=authorize_url)


@router.get("/gitlab/callback")
async def gitlab_callback(code: str):
    """Exchange authorization code for access token, fetch user info, issue JWT."""
    if not GITLAB_URL or not GITLAB_CLIENT_ID or not GITLAB_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="GitLab OAuth not configured")

    # 1. Exchange code for access_token
    async with httpx.AsyncClient(verify=False) as client:
        token_resp = await client.post(
            f"{GITLAB_URL}/oauth/token",
            data={
                "client_id": GITLAB_CLIENT_ID,
                "client_secret": GITLAB_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": f"{FRONTEND_ORIGIN}/auth/gitlab/callback",
            },
        )
        if token_resp.status_code != 200:
            logger.error("GitLab token exchange failed: %s", token_resp.text)
            raise HTTPException(status_code=401, detail="Failed to exchange authorization code")

        token_data = token_resp.json()
        access_token = token_data["access_token"]

        # 2. Fetch user profile
        user_resp = await client.get(
            f"{GITLAB_URL}/api/v4/user",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if user_resp.status_code != 200:
            logger.error("GitLab user fetch failed: %s", user_resp.text)
            raise HTTPException(status_code=401, detail="Failed to fetch GitLab user info")

        user_data = user_resp.json()

    # 3. Create JWT
    jwt_payload = {
        "gitlab_user_id": user_data["id"],
        "username": user_data["username"],
        "name": user_data.get("name", user_data["username"]),
        "avatar_url": user_data.get("avatar_url", ""),
        "gitlab_access_token": _encrypt_token(access_token),
    }
    jwt_token = create_jwt(jwt_payload)

    # 4. Redirect back to frontend with JWT in query param (frontend stores it)
    redirect_url = f"{FRONTEND_ORIGIN}/auth/callback?token={jwt_token}"
    return RedirectResponse(url=redirect_url)


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    """Return current user info from JWT."""
    return {
        "gitlab_user_id": current_user["gitlab_user_id"],
        "username": current_user["username"],
        "name": current_user["name"],
        "avatar_url": current_user.get("avatar_url", ""),
        "is_admin": current_user["username"] in ADMIN_USERNAMES,
    }
