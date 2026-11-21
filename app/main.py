import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.config import settings
import app.database as app_db
from app.database import Base, SessionLocal, engine, get_db, reset_current_user_id, set_current_user_id
from app.modules.assets import models as _asset_models  # noqa: F401
from app.modules.assets import service as asset_service
from app.modules.assets.router import router as assets_router
from app.modules.assistant import models as _assistant_models  # noqa: F401
from app.modules.assistant.router import bridge_router as assistant_bridge_router
from app.modules.assistant.router import router as assistant_router
from app.modules.auth import models as _auth_models  # noqa: F401
from app.modules.auth.dependencies import (
    PREAUTH_CSRF_COOKIE_NAME,
    get_optional_session,
    is_https_request,
    require_auth,
)
from app.modules.auth.router import router as auth_router
from app.modules.auth.security import generate_token, is_safe_redirect_url
from app.modules.friction import models as _friction_models  # noqa: F401
from app.modules.friction import service as friction_service
from app.modules.friction.router import router as friction_router
from app.modules.kitchen import models as _kitchen_models  # noqa: F401
from app.modules.kitchen import service as kitchen_service
from app.modules.kitchen.router import router as kitchen_router
from app.modules.settings import models as _settings_models  # noqa: F401
from app.modules.settings import service as settings_service
from app.modules.settings.router import router as settings_router
from app.modules.trainer import models as _trainer_models  # noqa: F401
from app.modules.trainer.router import router as trainer_router
from app.modules.wealth import models as _wealth_models  # noqa: F401
from app.modules.wealth import service as wealth_service
from app.modules.wealth.router import router as wealth_router, widget_router as wealth_widget_router


def _ensure_sqlite_columns(bind_engine) -> None:
    """Safely add new columns to existing SQLite tables without dropping user data."""
    if not str(bind_engine.url).startswith("sqlite"):
        return
    migrations = {
        "tracked_assets": [("user_id", "INTEGER NOT NULL DEFAULT 1")],
        "asset_valuations": [("user_id", "INTEGER NOT NULL DEFAULT 1")],
        "odometer_logs": [("user_id", "INTEGER NOT NULL DEFAULT 1")],
        "gear_consumables": [("user_id", "INTEGER NOT NULL DEFAULT 1")],
        "social_debts": [
            ("user_id", "INTEGER NOT NULL DEFAULT 1"),
            ("direction", "VARCHAR(16) NOT NULL DEFAULT 'owed_to_me'"),
            ("manual_paid_myr", "FLOAT NOT NULL DEFAULT 0.0"),
            ("monthly_installment_myr", "FLOAT"),
            ("match_keywords", "VARCHAR(255) NOT NULL DEFAULT ''"),
            ("start_date", "DATE"),
            ("due_day", "INTEGER"),
            ("match_installment_only", "BOOLEAN NOT NULL DEFAULT 0"),
            ("excluded_entry_ids", "TEXT NOT NULL DEFAULT ''"),
            ("interest_rate_pct", "FLOAT NOT NULL DEFAULT 0.0"),
            ("interest_type", "VARCHAR(16) NOT NULL DEFAULT 'none'"),
            ("tenure_months", "INTEGER"),
        ],
        "kitchen_inventory": [
            ("user_id", "INTEGER NOT NULL DEFAULT 1"),
            ("is_staple", "BOOLEAN NOT NULL DEFAULT 0"),
        ],
        "kitchen_taste_profiles": [
            ("texture", "VARCHAR(64) NOT NULL DEFAULT ''"),
            ("aroma", "VARCHAR(64) NOT NULL DEFAULT ''"),
            ("cooking_method", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ],
        "assistant_items": [
            ("attachment_data_url", "TEXT NOT NULL DEFAULT ''"),
            ("cost_tier", "VARCHAR(16) NOT NULL DEFAULT 'low_cost'"),
        ],
        "nutrition_targets": [("user_id", "INTEGER NOT NULL DEFAULT 1")],
        "workout_logs": [("user_id", "INTEGER NOT NULL DEFAULT 1")],
        "whitelisted_senders": [("user_id", "INTEGER NOT NULL DEFAULT 1")],
        "deletion_logs": [
            ("user_id", "INTEGER NOT NULL DEFAULT 1"),
            ("snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
        ],
        "bank_accounts": [("user_id", "INTEGER NOT NULL DEFAULT 1")],
        "category_model_state": [("user_id", "INTEGER NOT NULL DEFAULT 1")],
        "app_settings": [
            ("user_id", "INTEGER NOT NULL DEFAULT 1"),
            ("account_holder_names", "VARCHAR(500) NOT NULL DEFAULT ''"),
            ("live_listener_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
            ("auto_learn_categories", "BOOLEAN NOT NULL DEFAULT 1"),
            ("tng_pdf_password", "VARCHAR(100) NOT NULL DEFAULT ''"),
            ("visible_tabs", "VARCHAR(255) NOT NULL DEFAULT 'wealth,assets,kitchen,trainer,friction,assistant,resolve'"),
        ],
        "ledger_entries": [
            ("user_id", "INTEGER NOT NULL DEFAULT 1"),
            ("account_name", "VARCHAR(64) NOT NULL DEFAULT 'Other / Cash'"),
            ("is_self_transfer", "BOOLEAN NOT NULL DEFAULT 0"),
            ("is_claimable", "BOOLEAN NOT NULL DEFAULT 0"),
            ("remark", "TEXT NOT NULL DEFAULT ''"),
            ("conflict_resolved", "BOOLEAN NOT NULL DEFAULT 0"),
            ("manual_override", "BOOLEAN NOT NULL DEFAULT 0"),
            ("linked_entry_id", "INTEGER"),
        ],
        "category_rules": [
            ("user_id", "INTEGER NOT NULL DEFAULT 1"),
            ("flow_type", "VARCHAR(20) NOT NULL DEFAULT 'auto'"),
            ("monthly_budget_myr", "FLOAT"),
            ("color", "VARCHAR(16) NOT NULL DEFAULT ''"),
            ("day_min", "INTEGER"),
            ("day_max", "INTEGER"),
            ("amount_min", "FLOAT"),
            ("amount_max", "FLOAT"),
            ("tx_direction", "VARCHAR(16) NOT NULL DEFAULT 'any'"),
            ("account_filter", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ],
        "recurring_bills": [
            ("user_id", "INTEGER NOT NULL DEFAULT 1"),
            ("frequency", "VARCHAR(20) NOT NULL DEFAULT 'monthly'"),
            ("interval_days", "INTEGER NOT NULL DEFAULT 30"),
        ],
    }
    with bind_engine.begin() as conn:
        for table, cols in migrations.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}
            if not existing:
                continue
            for col_name, col_def in cols:
                if col_name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")

        # Upgrade single-tenant UNIQUE constraints to per-user composite UNIQUE constraints
        composite_uq_tables = {
            "tracked_assets": "uq_tracked_assets_user_name",
            "gear_consumables": "uq_gear_consumables_user_name",
            "nutrition_targets": "uq_nutrition_targets_user_date",
            "whitelisted_senders": "uq_whitelisted_senders_user_email",
            "ledger_entries": "uq_ledger_entries_user_hash",
            "bank_accounts": "uq_bank_accounts_user_name",
            "category_rules": "uq_category_rules_user_name",
            "recurring_bills": "uq_recurring_bills_user_name",
        }
        for tbl_name, uq_name in composite_uq_tables.items():
            ddl_row = conn.exec_driver_sql(
                f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{tbl_name}'"
            ).fetchone()
            if ddl_row and ddl_row[0] and uq_name not in ddl_row[0]:
                old_cols = [row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({tbl_name})").fetchall()]
                if old_cols:
                    col_list = ", ".join(f'"{c}"' for c in old_cols)
                    old_indexes = conn.exec_driver_sql(
                        f"SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='{tbl_name}' AND sql IS NOT NULL"
                    ).fetchall()
                    conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
                    for (idx_name,) in old_indexes:
                        conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{idx_name}"')
                    conn.exec_driver_sql(f'ALTER TABLE "{tbl_name}" RENAME TO "{tbl_name}_old"')
                    Base.metadata.tables[tbl_name].create(bind=conn)
                    conn.exec_driver_sql(f'INSERT INTO "{tbl_name}" ({col_list}) SELECT {col_list} FROM "{tbl_name}_old"')
                    conn.exec_driver_sql(f'DROP TABLE "{tbl_name}_old"')
                    conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        # Upgrade legacy ck_ledger_tx_type CHECK constraint to also allow 'repayment'
        ddl_row = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='ledger_entries'"
        ).fetchone()
        if ddl_row and ddl_row[0] and "ck_ledger_tx_type" in ddl_row[0] and "'repayment'" not in ddl_row[0]:
            new_ddl = ddl_row[0].replace(
                "tx_type IN ('expense', 'income')",
                "tx_type IN ('expense', 'income', 'repayment')",
            ).replace("CREATE TABLE ledger_entries", "CREATE TABLE ledger_entries_new", 1).replace(
                'CREATE TABLE "ledger_entries"', 'CREATE TABLE "ledger_entries_new"', 1
            )
            if new_ddl != ddl_row[0]:
                idx_rows = conn.exec_driver_sql(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='ledger_entries' AND sql IS NOT NULL"
                ).fetchall()
                conn.exec_driver_sql(new_ddl)
                conn.exec_driver_sql("INSERT INTO ledger_entries_new SELECT * FROM ledger_entries")
                conn.exec_driver_sql("DROP TABLE ledger_entries")
                conn.exec_driver_sql("ALTER TABLE ledger_entries_new RENAME TO ledger_entries")
                for (idx_sql,) in idx_rows:
                    conn.exec_driver_sql(idx_sql)

STATIC_DIR = Path(__file__).resolve().parent / "static"


async def _daily_wealth_sync_loop() -> None:
    """Silently run Wealth Ledger sync once per day at the user's configured hour."""
    while True:
        try:
            def _tick():
                with SessionLocal() as db:
                    cfg = settings_service.get_settings(db)
                    if not cfg.auto_sync_enabled:
                        return
                    now = datetime.now()
                    already_today = cfg.last_auto_sync_at and cfg.last_auto_sync_at.astimezone().date() == now.date()
                    if not already_today and now.hour >= cfg.auto_sync_hour:
                        settings_service.run_daily_auto_sync(db)

            await asyncio.to_thread(_tick)
        except Exception:
            logging.getLogger(__name__).exception("Daily wealth sync tick failed")
        await asyncio.sleep(900)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=app_db.engine)
    _ensure_sqlite_columns(app_db.engine)
    with SessionLocal() as db:
        asset_service.seed_default_assets(db)
        friction_service.seed_default_gear(db)
        settings_service.get_settings(db)
        wealth_service.seed_default_bank_accounts(db)
        wealth_service.seed_default_category_rules(db)
        wealth_service.reconcile_ledger_entries(db)
    task = asyncio.create_task(_daily_wealth_sync_loop())
    from app.modules.wealth.listener import wealth_listener
    listener_task = asyncio.create_task(wealth_listener.run_loop())
    from app.modules.assistant import bridge_manager
    bridge_manager.start_bridge()
    try:
        yield
    finally:
        bridge_manager.stop_bridge()
        task.cancel()
        listener_task.cancel()


app = FastAPI(
    title="Personal ERP — Full Modular Monolith",
    version="0.3.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def security_hardening_middleware(request: Request, call_next):
    path = request.url.path
    method = request.method.upper()

    # 1. Request body size limit (DoS defense)
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            size = int(content_length)
            limit = 1_048_576 if path.startswith("/api/auth") else 15 * 1024 * 1024
            if size > limit:
                return JSONResponse({"detail": "Payload too large."}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length."}, status_code=400)

    # 2. Fetch Metadata (Resource Isolation) — reject cross-site non-navigational requests before auth
    sec_fetch_site = request.headers.get("sec-fetch-site", "").lower()
    sec_fetch_mode = request.headers.get("sec-fetch-mode", "").lower()
    sec_fetch_dest = request.headers.get("sec-fetch-dest", "").lower()
    if sec_fetch_site and sec_fetch_site not in ("same-origin", "same-site", "none"):
        is_safe_nav = (
            sec_fetch_site == "cross-site"
            and sec_fetch_mode == "navigate"
            and method == "GET"
            and sec_fetch_dest not in ("object", "embed")
        )
        if not is_safe_nav:
            resp = JSONResponse({"detail": "Cross-site request blocked."}, status_code=403)
            resp.headers["Vary"] = "Sec-Fetch-Dest, Sec-Fetch-Mode, Sec-Fetch-Site"
            return resp

    # 3. Origin check on mutating requests (defense-in-depth for browsers without Fetch Metadata)
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        host = request.headers.get("host")
        if origin and host and urlparse(origin).netloc != host:
            return JSONResponse({"detail": "Cross-origin mutation blocked."}, status_code=403)

    tenant_tok = set_current_user_id(1)
    try:
        response = await call_next(request)
    finally:
        reset_current_user_id(tenant_tok)

    # 4. Security response headers
    response.headers["Vary"] = "Sec-Fetch-Dest, Sec-Fetch-Mode, Sec-Fetch-Site"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), geolocation=(), microphone=(), payment=(), usb=(), focus-without-user-activation=()"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"

    if is_https_request(request):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    if path.startswith("/api/") or path in ("/", "/login"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"

    if "content-security-policy" not in {k.lower() for k in response.headers.keys()}:
        response.headers["Content-Security-Policy"] = (
            "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'"
        )

    return response


app.include_router(auth_router)
app.include_router(assistant_router, dependencies=[Depends(require_auth)])
app.include_router(assistant_bridge_router)
app.include_router(wealth_router, dependencies=[Depends(require_auth)])
app.include_router(wealth_widget_router)
app.include_router(assets_router, dependencies=[Depends(require_auth)])
app.include_router(kitchen_router, dependencies=[Depends(require_auth)])
app.include_router(trainer_router, dependencies=[Depends(require_auth)])
app.include_router(friction_router, dependencies=[Depends(require_auth)])
app.include_router(settings_router, dependencies=[Depends(require_auth)])

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "email_mode": settings.email_mode,
        "carousell_mode": settings.carousell_mode,
    }


@app.get("/login")
def login_page(request: Request, next: str | None = None, db: Session = Depends(get_db)):
    if get_optional_session(request, db) is not None:
        target = next if is_safe_redirect_url(next) else "/"
        return RedirectResponse(url=target, status_code=303)

    nonce = generate_token(16)
    html = (STATIC_DIR / "login.html").read_text(encoding="utf-8").replace("__CSP_NONCE__", nonce)
    response = HTMLResponse(content=html)
    response.headers["Content-Security-Policy"] = (
        f"default-src 'self'; "
        f"script-src 'nonce-{nonce}' 'strict-dynamic' 'report-sample'; "
        f"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        f"font-src 'self' https://fonts.gstatic.com; "
        f"img-src 'self' data:; "
        f"connect-src 'self'; "
        f"object-src 'none'; "
        f"base-uri 'none'; "
        f"frame-ancestors 'none'; "
        f"form-action 'self'; "
        f"require-trusted-types-for 'script'"
    )
    pre_csrf = generate_token(32)
    response.set_cookie(
        key=PREAUTH_CSRF_COOKIE_NAME,
        value=pre_csrf,
        max_age=3600,
        httponly=False,
        secure=is_https_request(request),
        samesite="lax",
        path="/",
    )
    return response


@app.get("/")
def index(request: Request, db: Session = Depends(get_db)):
    if get_optional_session(request, db) is None:
        return RedirectResponse(url="/login", status_code=303)
    return FileResponse(STATIC_DIR / "index.html")

