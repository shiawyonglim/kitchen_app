import asyncio
import hmac
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
import app.database as app_db
from app.database import get_db, reset_current_user_id, set_current_user_id
from app.modules.auth import service as auth_service
from app.modules.auth.dependencies import (
    PREAUTH_CSRF_COOKIE_NAME,
    clear_auth_cookies,
    get_client_ip,
    get_optional_session,
    get_user_agent,
    is_https_request,
    require_auth,
    set_auth_cookies,
)
from app.modules.auth.google_oauth import (
    build_google_auth_url,
    exchange_code_and_verify,
    is_google_oauth_configured,
    save_google_tokens,
)
from app.modules.auth.models import UserSession
from app.modules.auth.rate_limit import auth_rate_limiter
from app.modules.auth.schemas import (
    AuditLogOut,
    AuthStatusResponse,
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    UserOut,
)
from app.modules.auth.security import generate_token, is_safe_redirect_url

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


def _enforce_rate_limit(scope: str, request: Request) -> None:
    ip = get_client_ip(request)
    allowed, retry_after = auth_rate_limiter.check_and_record(
        f"{scope}:{ip}",
        max_attempts=settings.rate_limit_max_attempts,
        window_seconds=settings.rate_limit_window_seconds,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please wait and try again.",
            headers={"Retry-After": str(retry_after)},
        )


def _verify_preauth_csrf(request: Request) -> None:
    cookie_token = request.cookies.get(PREAUTH_CSRF_COOKIE_NAME)
    if cookie_token:
        header_token = request.headers.get("x-csrf-token", "")
        if not header_token or not hmac.compare_digest(header_token, cookie_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or missing CSRF token.",
            )


@router.get("/csrf")
def issue_preauth_csrf(request: Request, response: Response):
    token = generate_token(32)
    response.set_cookie(
        key=PREAUTH_CSRF_COOKIE_NAME,
        value=token,
        max_age=3600,
        httponly=False,
        secure=is_https_request(request),
        samesite="lax",
        path="/",
    )
    return {"csrf_token": token}


@router.get("/status", response_model=AuthStatusResponse)
def auth_status(
    session: UserSession | None = Depends(get_optional_session),
    db: Session = Depends(get_db),
):
    count = auth_service.user_count(db)
    return AuthStatusResponse(
        authenticated=session is not None,
        needs_setup=count == 0,
        allow_public_signup=settings.allow_public_signup or count == 0,
        google_oauth_enabled=is_google_oauth_configured(),
        google_connected=bool(session is not None and session.user.google_token is not None),
        user=UserOut.model_validate(session.user) if session else None,
    )


def _seed_user_defaults(db: Session, user_id: int) -> None:
    from app.modules.settings import service as settings_service
    from app.modules.wealth import service as wealth_service

    tok = set_current_user_id(user_id)
    try:
        settings_service.get_settings(db)
        wealth_service.seed_default_bank_accounts(db)
        wealth_service.seed_default_category_rules(db)
    finally:
        reset_current_user_id(tok)


def _google_redirect_uri(request: Request) -> str:
    if settings.google_redirect_uri.strip():
        return settings.google_redirect_uri.strip()
    return str(request.url_for("google_callback"))


def _auto_discover_if_empty(db: Session) -> None:
    from datetime import date, timedelta
    from app.modules.wealth import service as wealth_service
    from app.modules.wealth.schemas import SenderWhitelistItem

    if wealth_service.list_whitelist(db):
        return
    end_d = date.today()
    start_d = end_d - timedelta(days=30)
    discovered = wealth_service.discover_senders(db, start_d, end_d)
    money_senders = [
        SenderWhitelistItem(
            sender_email=s.sender_email,
            display_name=s.display_name,
            category_hint=s.category_hint,
        )
        for s in discovered
        if s.is_money_related
    ]
    if money_senders:
        wealth_service.save_whitelist(db, money_senders)


async def _sync_google_emails_after_login(user_id: int) -> None:
    """Read Google emails into the Wealth Ledger in the background right after Google login."""
    def _run():
        from app.modules.settings import service as settings_service

        tok = set_current_user_id(user_id)
        try:
            with app_db.SessionLocal() as db:
                _auto_discover_if_empty(db)
                settings_service.run_daily_auto_sync(db)
        finally:
            reset_current_user_id(tok)

    try:
        await asyncio.to_thread(_run)
    except Exception:
        logging.getLogger(__name__).exception("Post-login Google email sync failed")


@router.get("/google/start")
def google_login_start(request: Request, next: str | None = None):
    _enforce_rate_limit("google_start", request)
    if not is_google_oauth_configured():
        return RedirectResponse(url="/login?error=google_not_configured", status_code=303)
    safe_next = next if is_safe_redirect_url(next) else "/"
    auth_url = build_google_auth_url(_google_redirect_uri(request), next_url=safe_next)
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/google/callback", name="google_callback")
async def google_login_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    _enforce_rate_limit("google_callback", request)
    if error or not code or not state:
        return RedirectResponse(url="/login?error=google_denied", status_code=303)

    try:
        tokens, profile, next_url = exchange_code_and_verify(
            code=code,
            state=state,
            redirect_uri=_google_redirect_uri(request),
        )
    except ValueError:
        return RedirectResponse(url="/login?error=google_failed", status_code=303)

    ip = get_client_ip(request)
    ua = get_user_agent(request)
    old_session = get_optional_session(request, db)
    if old_session is not None:
        user = old_session.user
    else:
        allow_create = auth_service.user_count(db) == 0 or settings.allow_public_signup
        user = auth_service.find_or_create_google_user(
            db,
            google_sub=profile["sub"],
            email=profile["email"],
            full_name=profile["name"],
            allow_create=allow_create,
            ip_address=ip,
            user_agent=ua,
        )
        if user is None:
            return RedirectResponse(url="/login?error=signup_closed", status_code=303)

    save_google_tokens(
        db,
        user=user,
        google_sub=profile["sub"],
        google_email=profile["email"],
        tokens=tokens,
    )

    if old_session is not None:
        auth_service.revoke_session(db, old_session)

    _seed_user_defaults(db, user.id)
    _, raw_token, csrf_token = auth_service.create_session(
        db, user=user, remember_me=True, ip_address=ip, user_agent=ua
    )

    target = next_url if is_safe_redirect_url(next_url) else "/"
    response = RedirectResponse(url=target, status_code=303)
    set_auth_cookies(
        response,
        request,
        raw_session_token=raw_token,
        csrf_token=csrf_token,
        remember_me=True,
    )

    _seed_user_defaults(db, user.id)
    asyncio.create_task(_sync_google_emails_after_login(user.id))
    return response


@router.post("/google/sync")
def sync_google_emails_now(
    session: UserSession = Depends(require_auth),
    db: Session = Depends(get_db),
):
    if session.user.google_token is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Google account connected to this user.",
        )
    from app.modules.settings import service as settings_service

    _auto_discover_if_empty(db)
    updated = settings_service.run_daily_auto_sync(db)
    return {
        "status": updated.last_auto_sync_status,
        "last_auto_sync_at": updated.last_auto_sync_at,
    }


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    _enforce_rate_limit("register", request)
    _verify_preauth_csrf(request)

    if auth_service.user_count(db) > 0 and not settings.allow_public_signup:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is closed. Please sign in with your owner account.",
        )

    ip = get_client_ip(request)
    ua = get_user_agent(request)
    try:
        user = auth_service.create_user(
            db,
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            ip_address=ip,
            user_agent=ua,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    _seed_user_defaults(db, user.id)
    _, raw_token, csrf_token = auth_service.create_session(
        db, user=user, remember_me=False, ip_address=ip, user_agent=ua
    )
    set_auth_cookies(
        response,
        request,
        raw_session_token=raw_token,
        csrf_token=csrf_token,
        remember_me=False,
    )
    return UserOut.model_validate(user)


@router.post("/login", response_model=UserOut)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    _enforce_rate_limit("login", request)
    _verify_preauth_csrf(request)

    ip = get_client_ip(request)
    ua = get_user_agent(request)
    user = auth_service.authenticate_user(
        db,
        email=payload.email,
        password=payload.password,
        ip_address=ip,
        user_agent=ua,
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    # Rotate any existing session on fresh login (session fixation defense)
    old_session = get_optional_session(request, db)
    if old_session is not None:
        auth_service.revoke_session(db, old_session)

    _seed_user_defaults(db, user.id)
    _, raw_token, csrf_token = auth_service.create_session(
        db, user=user, remember_me=payload.remember_me, ip_address=ip, user_agent=ua
    )
    set_auth_cookies(
        response,
        request,
        raw_session_token=raw_token,
        csrf_token=csrf_token,
        remember_me=payload.remember_me,
    )
    return UserOut.model_validate(user)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    session: UserSession = Depends(require_auth),
    db: Session = Depends(get_db),
):
    auth_service.revoke_session(db, session)
    auth_service.log_audit(
        db,
        "logout",
        user_id=session.user_id,
        email=session.user.email,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        detail="User signed out",
    )
    clear_auth_cookies(response, request)
    return {"status": "signed_out"}


@router.post("/logout-all")
def logout_all(
    request: Request,
    response: Response,
    session: UserSession = Depends(require_auth),
    db: Session = Depends(get_db),
):
    revoked = auth_service.revoke_all_user_sessions(db, session.user_id)
    auth_service.log_audit(
        db,
        "logout_all",
        user_id=session.user_id,
        email=session.user.email,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        detail=f"Revoked {revoked} active session(s)",
    )
    clear_auth_cookies(response, request)
    return {"status": "all_sessions_revoked", "revoked_count": revoked}


@router.get("/me", response_model=UserOut)
def me(session: UserSession = Depends(require_auth)):
    return UserOut.model_validate(session.user)


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    session: UserSession = Depends(require_auth),
    db: Session = Depends(get_db),
):
    _enforce_rate_limit("change_password", request)
    ip = get_client_ip(request)
    ua = get_user_agent(request)
    try:
        _, raw_token, csrf_token = auth_service.change_password(
            db,
            user=session.user,
            current_password=payload.current_password,
            new_password=payload.new_password,
            current_session=session,
            ip_address=ip,
            user_agent=ua,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    set_auth_cookies(
        response,
        request,
        raw_session_token=raw_token,
        csrf_token=csrf_token,
        remember_me=False,
    )
    return {"status": "password_updated"}


@router.get("/audit-logs", response_model=list[AuditLogOut])
def audit_logs(
    session: UserSession = Depends(require_auth),
    db: Session = Depends(get_db),
):
    logs = auth_service.list_recent_audit_logs(db, session.user_id, limit=20)
    return [AuditLogOut.model_validate(row) for row in logs]

