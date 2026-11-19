import hmac
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db, reset_current_user_id, set_current_user_id
from app.modules.auth import service as auth_service
from app.modules.auth.models import User, UserSession

SESSION_COOKIE_SECURE = "__Host-erp_session"
SESSION_COOKIE_DEV = "erp_session"
CSRF_COOKIE_NAME = "erp_csrf"
PREAUTH_CSRF_COOKIE_NAME = "erp_pre_csrf"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_https_request(request: Request) -> bool:
    if settings.cookie_secure is not None:
        return settings.cookie_secure
    if request.url.scheme == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return proto == "https"


def get_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def get_user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:256]


def read_session_cookie(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE_SECURE) or request.cookies.get(SESSION_COOKIE_DEV)


def read_bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        tok = auth[7:].strip()
        return tok or None
    return None


def set_auth_cookies(
    response: Response,
    request: Request,
    *,
    raw_session_token: str,
    csrf_token: str,
    remember_me: bool = False,
) -> None:
    secure = is_https_request(request)
    cookie_name = SESSION_COOKIE_SECURE if secure else SESSION_COOKIE_DEV
    max_age = (
        settings.session_remember_days * 86400
        if remember_me
        else settings.session_ttl_hours * 3600
    )
    response.set_cookie(
        key=cookie_name,
        value=raw_session_token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_auth_cookies(response: Response, request: Request) -> None:
    secure = is_https_request(request)
    for name in (SESSION_COOKIE_SECURE, SESSION_COOKIE_DEV, CSRF_COOKIE_NAME, PREAUTH_CSRF_COOKIE_NAME):
        response.delete_cookie(
            key=name,
            path="/",
            secure=secure if name == SESSION_COOKIE_SECURE else False,
            samesite="lax",
        )
    response.headers["Clear-Site-Data"] = '"cookies", "storage", "cache"'


def get_optional_session(
    request: Request, db: Session = Depends(get_db)
) -> UserSession | None:
    raw_token = read_bearer_token(request) or read_session_cookie(request)
    return auth_service.get_valid_session(db, raw_token)


def require_auth(
    request: Request, db: Session = Depends(get_db)
) -> UserSession:
    bearer_token = read_bearer_token(request)
    raw_token = bearer_token or read_session_cookie(request)
    session = auth_service.get_valid_session(db, raw_token)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    if bearer_token is None and request.method.upper() not in _SAFE_METHODS:
        header_csrf = request.headers.get("x-csrf-token", "")
        if not header_csrf or not hmac.compare_digest(header_csrf, session.csrf_token):
            auth_service.log_audit(
                db,
                "csrf_failed",
                user_id=session.user_id,
                email=session.user.email,
                ip_address=get_client_ip(request),
                user_agent=get_user_agent(request),
                detail=f"CSRF mismatch on {request.method} {request.url.path}",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF validation failed.",
            )

    request.state.user = session.user
    request.state.session = session
    db.info["user_id"] = session.user_id
    set_current_user_id(session.user_id)
    return session


def get_current_user(session: UserSession = Depends(require_auth)) -> User:
    return session.user

