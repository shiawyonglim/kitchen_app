from datetime import datetime, timedelta, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.modules.auth.models import AuthAuditLog, User, UserSession
from app.modules.auth.security import (
    DUMMY_PASSWORD_HASH,
    generate_token,
    hash_password,
    hash_token,
    mask_email,
    validate_email_format,
    validate_password_strength,
    verify_password,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def user_count(db: Session) -> int:
    return db.scalar(select(func.count(User.id))) or 0


def log_audit(
    db: Session,
    event_type: str,
    *,
    user_id: int | None = None,
    email: str = "",
    ip_address: str = "",
    user_agent: str = "",
    detail: str = "",
) -> None:
    entry = AuthAuditLog(
        user_id=user_id,
        event_type=event_type[:48],
        email_masked=mask_email(email) if email else "",
        ip_address=ip_address[:64],
        user_agent=user_agent[:256],
        detail=detail[:500],
    )
    db.add(entry)
    db.commit()


def create_user(
    db: Session,
    *,
    email: str,
    password: str,
    full_name: str = "Owner",
    ip_address: str = "",
    user_agent: str = "",
) -> User:
    clean_email = validate_email_format(email)
    validate_password_strength(password, email=clean_email)
    existing = db.scalar(select(User).where(User.email == clean_email))
    if existing is not None:
        raise ValueError("An account with this email already exists.")

    user = User(
        email=clean_email,
        full_name=(full_name or "Owner").strip()[:120] or "Owner",
        password_hash=hash_password(password),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log_audit(
        db,
        "register",
        user_id=user.id,
        email=clean_email,
        ip_address=ip_address,
        user_agent=user_agent,
        detail="Account created",
    )
    return user


def find_or_create_google_user(
    db: Session,
    *,
    google_sub: str,
    email: str,
    full_name: str,
    allow_create: bool,
    ip_address: str = "",
    user_agent: str = "",
) -> User | None:
    from app.modules.auth.models import GoogleOAuthToken

    clean_email = validate_email_format(email)
    token_row = db.scalar(select(GoogleOAuthToken).where(GoogleOAuthToken.google_sub == google_sub))
    if token_row is not None and token_row.user is not None:
        user = token_row.user
    else:
        user = db.scalar(select(User).where(User.email == clean_email))

    if user is None:
        if not allow_create:
            log_audit(
                db,
                "google_login_rejected",
                email=clean_email,
                ip_address=ip_address,
                user_agent=user_agent,
                detail="Public signup closed for unknown Google account",
            )
            return None
        user = User(
            email=clean_email,
            full_name=(full_name or "Owner").strip()[:120] or "Owner",
            password_hash="!google-oauth-only",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    if not user.is_active:
        return None

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = _utcnow()
    db.commit()
    db.refresh(user)
    log_audit(
        db,
        "google_login_success",
        user_id=user.id,
        email=clean_email,
        ip_address=ip_address,
        user_agent=user_agent,
        detail="Authenticated via Google OAuth",
    )
    return user


def authenticate_user(
    db: Session,
    *,
    email: str,
    password: str,
    ip_address: str = "",
    user_agent: str = "",
) -> User | None:
    try:
        clean_email = validate_email_format(email)
    except ValueError:
        verify_password(password, DUMMY_PASSWORD_HASH)
        return None

    user = db.scalar(select(User).where(User.email == clean_email))
    now = _utcnow()

    if user is None or not user.is_active:
        verify_password(password, DUMMY_PASSWORD_HASH)
        log_audit(
            db,
            "login_failed",
            email=clean_email,
            ip_address=ip_address,
            user_agent=user_agent,
            detail="Unknown or inactive account",
        )
        return None

    locked_until = _as_utc(user.locked_until)
    if locked_until is not None and locked_until > now:
        verify_password(password, DUMMY_PASSWORD_HASH)
        log_audit(
            db,
            "login_blocked_locked",
            user_id=user.id,
            email=clean_email,
            ip_address=ip_address,
            user_agent=user_agent,
            detail="Attempt during active lockout",
        )
        return None

    if not verify_password(password, user.password_hash):
        user.failed_attempts += 1
        if user.failed_attempts >= settings.max_failed_login_attempts:
            user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
            db.commit()
            log_audit(
                db,
                "account_locked",
                user_id=user.id,
                email=clean_email,
                ip_address=ip_address,
                user_agent=user_agent,
                detail=f"Locked for {settings.lockout_minutes}m after {user.failed_attempts} failed attempts",
            )
        else:
            db.commit()
            log_audit(
                db,
                "login_failed",
                user_id=user.id,
                email=clean_email,
                ip_address=ip_address,
                user_agent=user_agent,
                detail=f"Invalid password (attempt {user.failed_attempts})",
            )
        return None

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    db.commit()
    db.refresh(user)
    log_audit(
        db,
        "login_success",
        user_id=user.id,
        email=clean_email,
        ip_address=ip_address,
        user_agent=user_agent,
        detail="Authenticated",
    )
    return user


def find_or_create_google_user(
    db: Session,
    *,
    google_sub: str,
    email: str,
    full_name: str,
    allow_create: bool,
    ip_address: str = "",
    user_agent: str = "",
) -> User | None:
    from app.modules.auth.models import GoogleOAuthToken

    clean_email = validate_email_format(email)
    token_row = db.scalar(select(GoogleOAuthToken).where(GoogleOAuthToken.google_sub == google_sub))
    if token_row is not None and token_row.user is not None:
        user = token_row.user
    else:
        user = db.scalar(select(User).where(User.email == clean_email))

    if user is None:
        if not allow_create:
            log_audit(
                db,
                "google_login_rejected",
                email=clean_email,
                ip_address=ip_address,
                user_agent=user_agent,
                detail="Public signup closed for unknown Google account",
            )
            return None
        user = User(
            email=clean_email,
            full_name=(full_name or "Owner").strip()[:120] or "Owner",
            password_hash="!google-oauth-only",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    if not user.is_active:
        return None

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = _utcnow()
    db.commit()
    db.refresh(user)
    log_audit(
        db,
        "google_login_success",
        user_id=user.id,
        email=clean_email,
        ip_address=ip_address,
        user_agent=user_agent,
        detail="Authenticated via Google OAuth",
    )
    return user


def create_session(
    db: Session,
    *,
    user: User,
    remember_me: bool = False,
    ip_address: str = "",
    user_agent: str = "",
) -> tuple[UserSession, str, str]:
    raw_token = generate_token(32)
    csrf_token = generate_token(32)
    now = _utcnow()
    ttl = (
        timedelta(days=settings.session_remember_days)
        if remember_me
        else timedelta(hours=settings.session_ttl_hours)
    )
    session = UserSession(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        csrf_token=csrf_token,
        ip_address=ip_address[:64],
        user_agent=user_agent[:256],
        created_at=now,
        last_seen_at=now,
        expires_at=now + ttl,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session, raw_token, csrf_token


def get_valid_session(db: Session, raw_token: str | None) -> UserSession | None:
    if not raw_token or len(raw_token) > 256:
        return None
    token_digest = hash_token(raw_token)
    session = db.scalar(select(UserSession).where(UserSession.token_hash == token_digest))
    if session is None or session.revoked_at is not None:
        return None
    now = _utcnow()
    expires_at = _as_utc(session.expires_at)
    if expires_at is None or expires_at <= now:
        return None
    if session.user is None or not session.user.is_active:
        return None
    # Throttle last_seen_at writes to once per minute
    last_seen = _as_utc(session.last_seen_at)
    if last_seen is None or (now - last_seen) > timedelta(minutes=1):
        session.last_seen_at = now
        db.commit()
    return session


def revoke_session(db: Session, session: UserSession) -> None:
    if session.revoked_at is None:
        session.revoked_at = _utcnow()
        db.commit()


def revoke_all_user_sessions(
    db: Session, user_id: int, *, except_session_id: int | None = None
) -> int:
    now = _utcnow()
    stmt = select(UserSession).where(
        UserSession.user_id == user_id,
        UserSession.revoked_at.is_(None),
    )
    if except_session_id is not None:
        stmt = stmt.where(UserSession.id != except_session_id)
    sessions = db.scalars(stmt).all()
    for sess in sessions:
        sess.revoked_at = now
    db.commit()
    return len(sessions)


def change_password(
    db: Session,
    *,
    user: User,
    current_password: str,
    new_password: str,
    current_session: UserSession,
    ip_address: str = "",
    user_agent: str = "",
) -> tuple[UserSession, str, str]:
    if not verify_password(current_password, user.password_hash):
        log_audit(
            db,
            "password_change_failed",
            user_id=user.id,
            email=user.email,
            ip_address=ip_address,
            user_agent=user_agent,
            detail="Incorrect current password",
        )
        raise ValueError("Current password is incorrect.")

    validate_password_strength(new_password, email=user.email)
    if verify_password(new_password, user.password_hash):
        raise ValueError("New password must be different from your current password.")

    user.password_hash = hash_password(new_password)
    revoke_all_user_sessions(db, user.id)
    db.commit()

    new_sess, raw_token, csrf_token = create_session(
        db,
        user=user,
        remember_me=False,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    log_audit(
        db,
        "password_changed",
        user_id=user.id,
        email=user.email,
        ip_address=ip_address,
        user_agent=user_agent,
        detail="Password updated and all prior sessions revoked",
    )
    return new_sess, raw_token, csrf_token


def list_recent_audit_logs(db: Session, user_id: int, limit: int = 20) -> list[AuthAuditLog]:
    stmt = (
        select(AuthAuditLog)
        .where(AuthAuditLog.user_id == user_id)
        .order_by(AuthAuditLog.id.desc())
        .limit(min(limit, 50))
    )
    return list(db.scalars(stmt).all())

