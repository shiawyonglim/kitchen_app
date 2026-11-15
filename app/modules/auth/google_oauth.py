from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.modules.auth.models import GoogleOAuthToken, User

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_SCOPES = "openid email profile https://www.googleapis.com/auth/gmail.readonly"


def is_google_oauth_configured() -> bool:
    return bool(settings.google_client_id.strip() and settings.google_client_secret.strip())


class _OAuthStateStore:
    """Single-use, short-lived store for OAuth state, PKCE verifier, and OIDC nonce."""

    def __init__(self, ttl_seconds: int = 600) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[str, str, str, float]] = {}

    def create(self, next_url: str = "/") -> tuple[str, str, str]:
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(24)
        code_verifier = secrets.token_urlsafe(48)
        now = time.monotonic()
        with self._lock:
            expired = [k for k, v in self._pending.items() if now - v[3] > self._ttl]
            for k in expired:
                del self._pending[k]
            self._pending[state] = (code_verifier, nonce, next_url, now)
        return state, code_verifier, nonce

    def pop(self, state: str) -> tuple[str, str, str] | None:
        now = time.monotonic()
        with self._lock:
            entry = self._pending.pop(state, None)
        if entry is None:
            return None
        code_verifier, nonce, next_url, created_at = entry
        if now - created_at > self._ttl:
            return None
        return code_verifier, nonce, next_url


oauth_state_store = _OAuthStateStore()


def _pkce_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_google_auth_url(redirect_uri: str, next_url: str = "/") -> str:
    state, code_verifier, nonce = oauth_state_store.create(next_url=next_url)
    params = {
        "client_id": settings.google_client_id.strip(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": _pkce_s256(code_verifier),
        "code_challenge_method": "S256",
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def exchange_code_and_verify(
    *, code: str, state: str, redirect_uri: str
) -> tuple[dict, dict, str]:
    """Validate state, exchange code with PKCE, verify ID token audience/nonce, return (tokens, profile, next_url)."""
    pending = oauth_state_store.pop(state)
    if pending is None:
        raise ValueError("Invalid or expired OAuth state.")
    code_verifier, expected_nonce, next_url = pending

    with httpx.Client(timeout=15.0) as client:
        token_resp = client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id.strip(),
                "client_secret": settings.google_client_secret.strip(),
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            },
        )
        if token_resp.status_code != 200:
            raise ValueError("Failed to exchange Google authorization code.")
        tokens = token_resp.json()

        id_token = tokens.get("id_token")
        access_token = tokens.get("access_token")
        if not id_token or not access_token:
            raise ValueError("Incomplete token response from Google.")

        info_resp = client.get(GOOGLE_TOKENINFO_URL, params={"id_token": id_token})
        if info_resp.status_code != 200:
            raise ValueError("Unable to verify Google ID token.")
        claims = info_resp.json()

        if claims.get("aud") != settings.google_client_id.strip():
            raise ValueError("Google ID token audience mismatch.")
        token_nonce = claims.get("nonce", "")
        if not token_nonce or not hmac.compare_digest(str(token_nonce), expected_nonce):
            raise ValueError("Google ID token nonce mismatch.")
        email_verified = str(claims.get("email_verified", "")).lower() == "true"
        if not email_verified:
            raise ValueError("Google account email is not verified.")

        user_resp = client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        profile = user_resp.json() if user_resp.status_code == 200 else {}

    merged_profile = {
        "sub": str(profile.get("sub") or claims.get("sub") or ""),
        "email": str(profile.get("email") or claims.get("email") or "").strip().lower(),
        "name": str(profile.get("name") or claims.get("name") or "Owner").strip(),
        "email_verified": True,
    }
    if not merged_profile["sub"] or not merged_profile["email"]:
        raise ValueError("Google profile missing subject or email.")
    return tokens, merged_profile, next_url


def save_google_tokens(
    db: Session,
    *,
    user: User,
    google_sub: str,
    google_email: str,
    tokens: dict,
) -> GoogleOAuthToken:
    expires_in = int(tokens.get("expires_in", 3600))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in - 60))

    row = db.scalar(select(GoogleOAuthToken).where(GoogleOAuthToken.user_id == user.id))
    if row is None:
        row = GoogleOAuthToken(
            user_id=user.id,
            google_sub=google_sub,
            google_email=google_email,
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token") or "",
            scopes=tokens.get("scope") or GOOGLE_SCOPES,
            expires_at=expires_at,
        )
        db.add(row)
    else:
        row.google_sub = google_sub
        row.google_email = google_email
        row.access_token = tokens["access_token"]
        if tokens.get("refresh_token"):
            row.refresh_token = tokens["refresh_token"]
        if tokens.get("scope"):
            row.scopes = tokens["scope"]
        row.expires_at = expires_at
    db.commit()
    db.refresh(row)
    return row


def get_valid_google_access_token(db: Session, token_row: GoogleOAuthToken) -> str:
    now = datetime.now(timezone.utc)
    exp = token_row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp > now and token_row.access_token:
        return token_row.access_token

    if not token_row.refresh_token:
        raise ValueError("Google access token expired and no refresh token is stored. Please sign in with Google again.")

    with httpx.Client(timeout=15.0) as client:
        resp = client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.google_client_id.strip(),
                "client_secret": settings.google_client_secret.strip(),
                "refresh_token": token_row.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code != 200:
            raise ValueError("Failed to refresh Google access token. Please sign in with Google again.")
        data = resp.json()

    token_row.access_token = data["access_token"]
    expires_in = int(data.get("expires_in", 3600))
    token_row.expires_at = now + timedelta(seconds=max(60, expires_in - 60))
    db.commit()
    return token_row.access_token

