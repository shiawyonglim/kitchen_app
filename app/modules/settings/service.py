from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from sqlalchemy.orm import Session

from app.modules.settings.models import AppSettings
from app.modules.settings.schemas import UpdateSettingsRequest
from app.modules.wealth import service as wealth_service


def get_settings(db: Session) -> AppSettings:
    row = db.get(AppSettings, 1)
    if not row:
        row = AppSettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def update_settings(db: Session, req: UpdateSettingsRequest) -> AppSettings:
    row = get_settings(db)
    row.auto_sync_enabled = req.auto_sync_enabled
    row.live_listener_enabled = req.live_listener_enabled
    row.auto_learn_categories = req.auto_learn_categories
    row.auto_sync_hour = req.auto_sync_hour
    row.auto_sync_lookback_days = req.auto_sync_lookback_days
    row.monthly_budget_myr = req.monthly_budget_myr
    row.default_calories = req.default_calories
    row.default_protein_g = req.default_protein_g
    row.account_holder_names = req.account_holder_names.strip()
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    wealth_service.reconcile_ledger_entries(db)
    return row


def run_daily_auto_sync(db: Session) -> AppSettings:
    """Sync whitelisted wealth senders over the configured lookback window."""
    row = get_settings(db)
    today = date.today()
    start = today - timedelta(days=row.auto_sync_lookback_days)
    try:
        res = wealth_service.sync_whitelisted_transactions(db, start, today)
        row.last_auto_sync_status = (
            f"OK: scanned {res.scanned_emails}, +{res.inserted_count} new, {res.skipped_duplicates} dup"
        )
    except Exception as exc:
        row.last_auto_sync_status = f"Error: {str(exc)[:180]}"
    row.last_auto_sync_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row

