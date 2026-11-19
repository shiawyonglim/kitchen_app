import asyncio
import hmac
from datetime import date, datetime, timezone
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db, reset_current_user_id, set_current_user_id
from app.modules.wealth import service
from app.modules.wealth.statement_parser import PdfLockedError
from app.modules.wealth.schemas import (
    BankAccountBalanceOut,
    BankAccountUpsertRequest,
    CategoryRuleOut,
    CategoryRuleUpsertRequest,
    ConflictGroupOut,
    DateRangeRequest,
    DeletionLogOut,
    DiscoveredSender,
    LedgerEntryOut,
    LinkRepaymentsRequest,
    ManualLedgerRequest,
    RecurringBillOut,
    RecurringBillUpsertRequest,
    ResolveConflictRequest,
    SaveWhitelistRequest,
    SenderWhitelistItem,
    StatementBackfillResult,
    SyncRequest,
    SyncResult,
    UpdateEntryRequest,
    WealthSummary,
)

router = APIRouter(prefix="/api/wealth", tags=["Wealth Ledger"])
widget_router = APIRouter(prefix="/api/wealth", tags=["Mobile Widget"])


@router.post("/discover-senders", response_model=list[DiscoveredSender])
def discover_senders(req: DateRangeRequest, db: Session = Depends(get_db)):
    if req.end_date < req.start_date:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")
    try:
        return service.discover_senders(db, req.start_date, req.end_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _serialize_whitelist(db: Session, rows) -> list[SenderWhitelistItem]:
    counts = service.get_sender_received_counts(db)
    return [
        SenderWhitelistItem(
            sender_email=r.sender_email,
            display_name=r.display_name,
            category_hint=r.category_hint,
            received_count=counts.get(r.sender_email.lower(), 0),
        )
        for r in rows
    ]


@router.get("/whitelist", response_model=list[SenderWhitelistItem])
def get_whitelist(db: Session = Depends(get_db)):
    return _serialize_whitelist(db, service.list_whitelist(db))


@router.post("/whitelist", response_model=list[SenderWhitelistItem])
def save_whitelist(req: SaveWhitelistRequest, db: Session = Depends(get_db)):
    return _serialize_whitelist(db, service.save_whitelist(db, req.senders))


@router.post("/whitelist/add", response_model=list[SenderWhitelistItem])
def add_whitelist_sender(item: SenderWhitelistItem, db: Session = Depends(get_db)):
    rows = service.add_whitelist_sender(db, item)
    return [
        SenderWhitelistItem(
            sender_email=r.sender_email,
            display_name=r.display_name,
            category_hint=r.category_hint,
        )
        for r in rows
    ]


@router.delete("/whitelist", response_model=list[SenderWhitelistItem])
def remove_whitelist_sender(sender_email: str, db: Session = Depends(get_db)):
    rows = service.remove_whitelist_sender(db, sender_email)
    return [
        SenderWhitelistItem(
            sender_email=r.sender_email,
            display_name=r.display_name,
            category_hint=r.category_hint,
        )
        for r in rows
    ]


@router.delete("/entries")
def clear_entries(db: Session = Depends(get_db)):
    deleted = service.clear_entries(db)
    return {"deleted": deleted}


@router.delete("/entries/{entry_id}")
def delete_entry(entry_id: int, remark: str = "", db: Session = Depends(get_db)):
    if not service.delete_entry(db, entry_id, remark=remark):
        raise HTTPException(status_code=404, detail="Ledger entry not found")
    return {"deleted": True}


@router.patch("/entries/{entry_id}", response_model=LedgerEntryOut)
def update_entry(entry_id: int, req: UpdateEntryRequest, db: Session = Depends(get_db)):
    updated = service.update_entry(db, entry_id, req)
    if not updated:
        raise HTTPException(status_code=404, detail="Ledger entry not found")
    return updated


@router.get("/deletions", response_model=list[DeletionLogOut])
def list_deletions(db: Session = Depends(get_db)):
    return service.list_deletion_logs(db)


@router.post("/deletions/{log_id}/restore", response_model=LedgerEntryOut)
def restore_deletion(log_id: int, db: Session = Depends(get_db)):
    restored = service.restore_deleted_entry(db, log_id)
    if not restored:
        raise HTTPException(status_code=404, detail="Deletion log entry not found")
    return restored


@router.patch("/entries/{entry_id}/toggle-type", response_model=LedgerEntryOut)
def toggle_entry_type(entry_id: int, db: Session = Depends(get_db)):
    updated = service.toggle_entry_type(db, entry_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Ledger entry not found")
    return updated


@router.post("/entries/{entry_id}/link", response_model=LedgerEntryOut)
def link_entry_repayments(entry_id: int, req: LinkRepaymentsRequest, db: Session = Depends(get_db)):
    updated = service.link_repayments(db, entry_id, req.repayment_ids, split_count=req.split_count)
    if not updated:
        raise HTTPException(status_code=404, detail="Ledger entry not found")
    return updated


@router.post("/entries/{entry_id}/accept-suggested-link", response_model=LedgerEntryOut)
def accept_suggested_link(entry_id: int, db: Session = Depends(get_db)):
    updated = service.accept_suggested_repayment_link(db, entry_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Ledger entry not found")
    return updated


@router.delete("/entries/{entry_id}/link", response_model=LedgerEntryOut)
def unlink_entry_repayment(entry_id: int, db: Session = Depends(get_db)):
    updated = service.unlink_repayment(db, entry_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Ledger entry not found")
    return updated


@router.get("/entries/{entry_id}/email-link")
def open_entry_email(entry_id: int, db: Session = Depends(get_db)):
    url = service.resolve_entry_email_url(db, entry_id)
    if not url:
        raise HTTPException(status_code=404, detail="Ledger entry not found")
    return RedirectResponse(url=url, status_code=302)


@router.get("/accounts", response_model=list[BankAccountBalanceOut])
def list_accounts(db: Session = Depends(get_db)):
    return service.list_bank_accounts(db)


@router.put("/accounts", response_model=BankAccountBalanceOut)
def upsert_account(req: BankAccountUpsertRequest, db: Session = Depends(get_db)):
    return service.upsert_bank_account(db, req)


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    if not service.delete_bank_account(db, account_id):
        raise HTTPException(status_code=404, detail="Bank account not found")
    return {"deleted": True}


@router.post("/reconcile")
def reconcile_entries(db: Session = Depends(get_db)):
    return service.reconcile_ledger_entries(db)


@router.get("/categories", response_model=list[CategoryRuleOut])
def list_categories(db: Session = Depends(get_db)):
    return service.list_category_rules(db)


@router.put("/categories", response_model=CategoryRuleOut)
def upsert_category(req: CategoryRuleUpsertRequest, db: Session = Depends(get_db)):
    return service.upsert_category_rule(db, req)


@router.delete("/categories/{rule_id}")
def delete_category(rule_id: int, db: Session = Depends(get_db)):
    if not service.delete_category_rule(db, rule_id):
        raise HTTPException(status_code=404, detail="Category rule not found")
    return {"deleted": True}


@router.get("/bills", response_model=list[RecurringBillOut])
def list_bills(month: str | None = None, db: Session = Depends(get_db)):
    return service.list_recurring_bills(db, month=month)


@router.post("/bills", response_model=RecurringBillOut)
def upsert_bill(req: RecurringBillUpsertRequest, db: Session = Depends(get_db)):
    return service.upsert_recurring_bill(db, req)


@router.delete("/bills/{bill_id}")
def delete_bill(bill_id: int, db: Session = Depends(get_db)):
    if not service.delete_recurring_bill(db, bill_id):
        raise HTTPException(status_code=404, detail="Recurring bill not found")
    return {"deleted": True}


@router.get("/conflicts", response_model=list[ConflictGroupOut])
def list_conflicts(window_minutes: int = 5, db: Session = Depends(get_db)):
    return service.list_conflict_groups(db, window_minutes=window_minutes)


@router.post("/conflicts/resolve")
def resolve_conflicts(req: ResolveConflictRequest, db: Session = Depends(get_db)):
    return service.resolve_conflict_group(db, req)


@router.post("/sync", response_model=SyncResult)
def sync_transactions(req: SyncRequest, db: Session = Depends(get_db)):
    if req.end_date < req.start_date:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")
    try:
        return service.sync_whitelisted_transactions(
            db, req.start_date, req.end_date, account_name=req.account_name
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/backfill-statement", response_model=StatementBackfillResult)
async def backfill_statement(
    file: UploadFile = File(...),
    bank_name: str = Form("Bank Statement"),
    pdf_password: str = Form(""),
    db: Session = Depends(get_db),
):
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded statement file is empty")
    try:
        return service.backfill_from_statement(
            db,
            file.filename or "statement.csv",
            file_bytes,
            bank_name,
            pdf_password=pdf_password or None,
        )
    except PdfLockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/entries/manual", response_model=LedgerEntryOut)
def add_manual_entry(req: ManualLedgerRequest, db: Session = Depends(get_db)):
    return service.add_manual_entry(db, req)


@router.get("/entries", response_model=list[LedgerEntryOut])
def list_entries(
    start_date: date | None = None,
    end_date: date | None = None,
    month: str | None = None,
    db: Session = Depends(get_db),
):
    return service.list_entries(db, start_date=start_date, end_date=end_date, month=month)


@router.get("/summary", response_model=WealthSummary)
def get_summary(
    start_date: date | None = None,
    end_date: date | None = None,
    month: str | None = None,
    db: Session = Depends(get_db),
):
    return service.get_summary(db, start_date=start_date, end_date=end_date, month=month)


@router.get("/export.csv")
def export_csv(
    month: str | None = None,
    claimable_only: bool = False,
    db: Session = Depends(get_db),
):
    csv_text = service.export_entries_csv(db, month=month, claimable_only=claimable_only)
    tag = month or "all"
    prefix = "claimables" if claimable_only else "wealth-ledger"
    filename = f"{prefix}-{tag}.csv"
    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/listener-status")
def get_listener_status():
    from app.modules.wealth.listener import wealth_listener

    return wealth_listener.snapshot()


@router.get("/stream")
async def stream_wealth_events():
    from app.modules.wealth.listener import wealth_listener

    q = wealth_listener.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            wealth_listener.unsubscribe(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/model")
def get_category_model(db: Session = Depends(get_db)):
    from app.modules.wealth import category_model

    return {
        "model": category_model.summarize_model_state(category_model.get_model_state(db)),
        "suggestions": category_model.preview_suggestions(db),
    }


@router.post("/model/train")
def train_category_model_endpoint(db: Session = Depends(get_db)):
    from app.modules.wealth import category_model

    summary = category_model.train_category_model(db)
    return {
        "model": summary,
        "suggestions": category_model.preview_suggestions(db),
    }


@router.post("/model/apply")
def apply_category_model_endpoint(db: Session = Depends(get_db)):
    from app.modules.wealth import category_model

    updated = category_model.apply_suggestions(db)
    return {
        "updated": updated,
        "model": category_model.summarize_model_state(category_model.get_model_state(db)),
        "suggestions": category_model.preview_suggestions(db),
    }


@router.post("/notifications/test")
def test_ntfy_notification():
    from app.modules.wealth.notifications import send_ntfy_notification

    configured = bool((settings.ntfy_topic_url or "").strip())
    sent = send_ntfy_notification(
        "Personal ERP Test",
        "Push notifications are connected to your iPhone!",
        tags="white_check_mark",
    )
    return {"configured": configured, "sent": sent}


@widget_router.get("/widget-summary")
def get_widget_summary(
    authorization: str = Header(default=""),
    x_widget_token: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """Lightweight JSON summary for iPhone widgets, authenticated via Bearer session token or WIDGET_API_TOKEN."""
    from app.modules.auth import service as auth_service
    from app.modules.settings import service as settings_service

    expected = (settings.widget_api_token or "").strip()
    bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    provided = bearer or x_widget_token.strip()

    user_id: int | None = None
    if expected and provided and hmac.compare_digest(provided, expected):
        user_id = settings.widget_user_id
    elif bearer:
        sess = auth_service.get_valid_session(db, bearer)
        if sess is not None:
            user_id = sess.user_id

    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or unconfigured widget API token")

    tok = set_current_user_id(user_id)
    db.info["user_id"] = user_id
    try:
        today = date.today()
        month_str = today.strftime("%Y-%m")
        today_str = today.isoformat()
        summary = service.get_summary(db, month=month_str)
        app_cfg = settings_service.get_settings(db)
        budget = round(float(app_cfg.monthly_budget_myr or 0.0), 2)

        today_pt = next((p for p in summary.daily_series if p.date == today_str), None)
        today_expense = round(today_pt.expense_myr, 2) if today_pt else 0.0
        today_income = round(today_pt.income_myr, 2) if today_pt else 0.0
        month_expense = round(summary.total_expense_myr, 2)
        month_income = round(summary.total_income_myr, 2)
        remaining = round(budget - month_expense, 2) if budget > 0 else 0.0
        used_pct = round((month_expense / budget) * 100.0, 1) if budget > 0 else 0.0

        entries = service.list_entries(db, month=month_str)
        recent = [
            {
                "merchant": e.display_merchant or e.merchant,
                "category": e.category,
                "tx_type": e.tx_type,
                "amount_myr": round(float(e.net_amount_myr if e.net_amount_myr is not None else e.amount_myr), 2),
                "account_name": e.account_name,
                "occurred_at": e.occurred_at.isoformat(),
            }
            for e in entries
            if not e.is_self_transfer
        ][:5]

        return {
            "month": month_str,
            "today": today_str,
            "today_expense_myr": today_expense,
            "today_income_myr": today_income,
            "month_expense_myr": month_expense,
            "month_income_myr": month_income,
            "monthly_budget_myr": budget,
            "budget_remaining_myr": remaining,
            "budget_used_pct": used_pct,
            "daily_burn_myr": round(summary.rolling_30d_daily_burn_myr, 2),
            "burn_pace_ratio": summary.burn_pace_ratio,
            "remaining_fixed_bills_myr": round(summary.remaining_fixed_bills_myr, 2),
            "accounts": [
                {"name": a.name, "balance_myr": round(a.current_balance_myr, 2)}
                for a in summary.account_balances[:4]
            ],
            "recent": recent,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        reset_current_user_id(tok)

