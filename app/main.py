import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.modules.assets import models as _asset_models  # noqa: F401
from app.modules.assets import service as asset_service
from app.modules.assets.router import router as assets_router
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
from app.modules.wealth.router import router as wealth_router


def _ensure_sqlite_columns(bind_engine) -> None:
    """Safely add new columns to existing SQLite tables without dropping user data."""
    if not str(bind_engine.url).startswith("sqlite"):
        return
    migrations = {
        "app_settings": [
            ("account_holder_names", "VARCHAR(500) NOT NULL DEFAULT ''"),
            ("live_listener_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
            ("auto_learn_categories", "BOOLEAN NOT NULL DEFAULT 1"),
        ],
        "ledger_entries": [
            ("account_name", "VARCHAR(64) NOT NULL DEFAULT 'Other / Cash'"),
            ("is_self_transfer", "BOOLEAN NOT NULL DEFAULT 0"),
            ("is_claimable", "BOOLEAN NOT NULL DEFAULT 0"),
            ("remark", "TEXT NOT NULL DEFAULT ''"),
            ("conflict_resolved", "BOOLEAN NOT NULL DEFAULT 0"),
            ("manual_override", "BOOLEAN NOT NULL DEFAULT 0"),
            ("linked_entry_id", "INTEGER"),
        ],
        "deletion_logs": [
            ("snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
        ],
        "category_rules": [
            ("flow_type", "VARCHAR(20) NOT NULL DEFAULT 'auto'"),
            ("monthly_budget_myr", "FLOAT"),
        ],
        "recurring_bills": [
            ("frequency", "VARCHAR(20) NOT NULL DEFAULT 'monthly'"),
            ("interval_days", "INTEGER NOT NULL DEFAULT 30"),
        ],
    }
    with bind_engine.begin() as conn:
        for table, cols in migrations.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}
            for col_name, col_def in cols:
                if col_name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
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
            pass
        await asyncio.sleep(900)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns(engine)
    with SessionLocal() as db:
        asset_service.seed_default_assets(db)
        kitchen_service.seed_default_kitchen(db)
        friction_service.seed_default_gear(db)
        settings_service.get_settings(db)
        wealth_service.seed_default_bank_accounts(db)
        wealth_service.seed_default_category_rules(db)
        wealth_service.reconcile_ledger_entries(db)
    task = asyncio.create_task(_daily_wealth_sync_loop())
    from app.modules.wealth.listener import wealth_listener
    listener_task = asyncio.create_task(wealth_listener.run_loop())
    try:
        yield
    finally:
        task.cancel()
        listener_task.cancel()


app = FastAPI(
    title="Personal ERP — Full Modular Monolith",
    version="0.3.0",
    lifespan=lifespan,
)

app.include_router(wealth_router)
app.include_router(assets_router)
app.include_router(kitchen_router)
app.include_router(trainer_router)
app.include_router(friction_router)
app.include_router(settings_router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "email_mode": settings.email_mode,
        "carousell_mode": settings.carousell_mode,
    }


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")

