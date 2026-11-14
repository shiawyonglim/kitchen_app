import asyncio
from datetime import date
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
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

