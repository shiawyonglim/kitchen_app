from __future__ import annotations

import calendar
import csv
import hashlib
import io
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.modules.settings.models import AppSettings
from app.modules.wealth.email_client import ImapEmailProvider, get_email_provider
from app.modules.wealth.models import (
    BankAccount,
    CategoryRule,
    DeletionLog,
    LedgerEntry,
    RecurringBill,
    WhitelistedSender,
)
from app.modules.wealth.parser import (
    extract_merchant_from_text,
    infer_account_name,
    infer_category_hint,
    is_self_transfer_match,
    match_category_rule_with_flow,
    match_category_rules,
    normalize_merchant_display,
    parse_transaction_emails,
    score_money_sender,
)
from app.modules.wealth.schemas import (
    BankAccountBalanceOut,
    BankAccountUpsertRequest,
    CategoryRuleUpsertRequest,
    ConflictGroupOut,
    DailyCashFlowPoint,
    DiscoveredSender,
    LedgerEntryOut,
    ManualLedgerRequest,
    MonthlyCategoryPoint,
    RecurringBillOut,
    RecurringBillUpsertRequest,
    ResolveConflictRequest,
    SenderWhitelistItem,
    StatementBackfillResult,
    SyncResult,
    TransferCounterpartyStat,
    UpdateEntryRequest,
    WealthSummary,
)
from app.modules.wealth.statement_parser import PdfLockedError, parse_statement_file


def _get_holder_names(db: Session) -> str:
    row = db.scalar(select(AppSettings))
    return row.account_holder_names if row else ""


def _resolve_email_provider(db: Session):
    try:
        return get_email_provider(db)
    except TypeError:
        return get_email_provider()


def discover_senders(db: Session, start_date: date, end_date: date) -> list[DiscoveredSender]:
    """Scan inbox headers in the date range and rank money/bank senders above junk."""
    provider = _resolve_email_provider(db)
    emails = provider.fetch_messages(start_date=start_date, end_date=end_date, headers_only=True)

    active_whitelist = {
        row.sender_email.lower()
        for row in db.scalars(select(WhitelistedSender).where(WhitelistedSender.is_active.is_(True))).all()
    }

    grouped: dict[str, dict] = {}
    for msg in emails:
        key = msg.sender_email.lower()
        if key not in grouped:
            grouped[key] = {
                "sender_email": key,
                "display_name": msg.sender_name,
                "subjects": [],
                "count": 0,
            }
        grouped[key]["count"] += 1
        grouped[key]["subjects"].append(msg.subject)

    discovered: list[DiscoveredSender] = []
    for info in grouped.values():
        is_money, score, cat_hint = score_money_sender(
            info["sender_email"], info["display_name"], info["subjects"]
        )
        discovered.append(
            DiscoveredSender(
                sender_email=info["sender_email"],
                display_name=info["display_name"],
                email_count=info["count"],
                sample_subject=info["subjects"][0] if info["subjects"] else "",
                is_money_related=is_money,
                money_score=score,
                is_whitelisted=info["sender_email"] in active_whitelist,
                category_hint=cat_hint,
            )
        )

    discovered.sort(key=lambda s: (s.is_money_related, s.money_score, s.email_count), reverse=True)
    return discovered


def save_whitelist(db: Session, senders: list[SenderWhitelistItem]) -> list[WhitelistedSender]:
    """Persist the user's selected money-related senders in an ACID transaction."""
    selected_map = {s.sender_email.lower().strip(): s for s in senders}

    existing_rows = db.scalars(select(WhitelistedSender)).all()
    existing_by_email = {row.sender_email.lower(): row for row in existing_rows}

    for email_addr, row in existing_by_email.items():
        if email_addr in selected_map:
            item = selected_map[email_addr]
            row.display_name = item.display_name
            row.category_hint = item.category_hint
            row.is_active = True
        else:
            row.is_active = False

    for email_addr, item in selected_map.items():
        if email_addr not in existing_by_email:
            db.add(
                WhitelistedSender(
                    sender_email=email_addr,
                    display_name=item.display_name,
                    category_hint=item.category_hint,
                    is_active=True,
                )
            )

    db.commit()
    return list(
        db.scalars(
            select(WhitelistedSender)
            .where(WhitelistedSender.is_active.is_(True))
            .order_by(WhitelistedSender.display_name)
        ).all()
    )


def list_whitelist(db: Session) -> list[WhitelistedSender]:
    return list(
        db.scalars(
            select(WhitelistedSender)
            .where(WhitelistedSender.is_active.is_(True))
            .order_by(WhitelistedSender.display_name)
        ).all()
    )


def get_sender_received_counts(db: Session) -> dict[str, int]:
    """Return count of synced ledger entries grouped by lowercase sender_email."""
    from sqlalchemy import func

    rows = db.execute(
        select(func.lower(LedgerEntry.sender_email), func.count(LedgerEntry.id)).group_by(
            func.lower(LedgerEntry.sender_email)
        )
    ).all()
    return {email: int(cnt) for email, cnt in rows if email}


def add_whitelist_sender(db: Session, item: SenderWhitelistItem) -> list[WhitelistedSender]:
    """Add or re-activate a single custom sender email in the allowlist."""
    email_addr = item.sender_email.lower().strip()
    existing = db.scalar(select(WhitelistedSender).where(WhitelistedSender.sender_email == email_addr))
    if existing:
        existing.display_name = item.display_name
        existing.category_hint = item.category_hint
        existing.is_active = True
    else:
        db.add(
            WhitelistedSender(
                sender_email=email_addr,
                display_name=item.display_name,
                category_hint=item.category_hint,
                is_active=True,
            )
        )
    db.commit()
    return list_whitelist(db)


def remove_whitelist_sender(db: Session, sender_email: str) -> list[WhitelistedSender]:
    """Deactivate a sender from the trusted allowlist."""
    email_addr = sender_email.lower().strip()
    existing = db.scalar(select(WhitelistedSender).where(WhitelistedSender.sender_email == email_addr))
    if existing:
        existing.is_active = False
        db.commit()
    return list_whitelist(db)


def clear_entries(db: Session) -> int:
    """Delete all ledger entries (useful when switching from demo to live inbox)."""
    count = db.query(LedgerEntry).delete()
    db.commit()
    return count


def delete_entry(db: Session, entry_id: int, remark: str = "") -> bool:
    entry = db.get(LedgerEntry, entry_id)
    if not entry:
        return False
    db.add(
        DeletionLog(
            original_entry_id=entry.id,
            merchant=entry.merchant,
            category=entry.category,
            account_name=entry.account_name,
            tx_type=entry.tx_type,
            amount_myr=entry.amount_myr,
            occurred_at=entry.occurred_at,
            remark=(remark or "").strip(),
            snapshot_json={
                "idempotency_hash": entry.idempotency_hash,
                "sender_email": entry.sender_email,
                "subject": entry.subject,
                "is_self_transfer": entry.is_self_transfer,
                "is_claimable": entry.is_claimable,
                "remark": entry.remark,
                "raw_payload": entry.raw_payload or {},
            },
        )
    )
    db.delete(entry)
    db.commit()
    return True


def restore_deleted_entry(db: Session, log_id: int) -> LedgerEntry | None:
    """Restore a previously deleted ledger entry from DeletionLog back into LedgerEntry."""
    log = db.get(DeletionLog, log_id)
    if not log:
        return None
    snap = log.snapshot_json if isinstance(log.snapshot_json, dict) else {}
    idem_hash = snap.get("idempotency_hash")
    if not idem_hash or db.scalar(select(LedgerEntry).where(LedgerEntry.idempotency_hash == idem_hash)):
        idem_hash = f"restored:{log.id}:{uuid4().hex}"
    subject = snap.get("subject") or f"Restored from deletion log (#{log.original_entry_id})"
    is_self = (
        bool(snap["is_self_transfer"])
        if "is_self_transfer" in snap
        else is_self_transfer_match(log.merchant, subject, _get_holder_names(db))
    )
    entry = LedgerEntry(
        idempotency_hash=idem_hash,
        sender_email=snap.get("sender_email") or "restored@manual",
        merchant=log.merchant,
        category=log.category,
        tx_type=log.tx_type,
        amount_myr=log.amount_myr,
        occurred_at=log.occurred_at,
        subject=subject,
        account_name=log.account_name,
        is_self_transfer=is_self,
        is_claimable=bool(snap.get("is_claimable", False)),
        remark=snap.get("remark") or "",
        raw_payload=snap.get("raw_payload") if isinstance(snap.get("raw_payload"), dict) else {},
    )
    db.add(entry)
    db.delete(log)
    db.commit()
    db.refresh(entry)
    return entry


def update_entry(db: Session, entry_id: int, req: UpdateEntryRequest) -> LedgerEntry | None:
    entry = db.get(LedgerEntry, entry_id)
    if not entry:
        return None
    if req.category is not None:
        entry.category = req.category.strip()
        entry.manual_override = True
        if entry.category.lower() == "business claim" and req.is_claimable is None:
            entry.is_claimable = True
        if req.tx_type is None:
            rule = db.scalar(select(CategoryRule).where(CategoryRule.name == entry.category))
            if rule and rule.flow_type in ("income", "repayment", "expense"):
                entry.tx_type = rule.flow_type
    if req.tx_type is not None:
        entry.tx_type = req.tx_type
        entry.manual_override = True
    if req.is_claimable is not None:
        entry.is_claimable = req.is_claimable
    if req.is_self_transfer is not None:
        entry.is_self_transfer = req.is_self_transfer
        entry.manual_override = True
    if req.remark is not None:
        entry.remark = req.remark.strip()
    if req.split_count is not None:
        payload = dict(entry.raw_payload) if isinstance(entry.raw_payload, dict) else {}
        if req.split_count <= 1:
            payload.pop("split_count", None)
        else:
            payload["split_count"] = int(req.split_count)
        entry.raw_payload = payload
    db.commit()
    db.refresh(entry)
    if req.category is not None:
        cfg = db.scalar(select(AppSettings))
        if cfg is None or cfg.auto_learn_categories:
            from app.modules.wealth import category_model

            category_model.train_category_model(db)
    all_rows = list(db.scalars(select(LedgerEntry)).all())
    _enrich_linked_fields(all_rows, [entry])
    return entry


def list_deletion_logs(db: Session, limit: int = 100) -> list[DeletionLog]:
    return list(
        db.scalars(select(DeletionLog).order_by(DeletionLog.deleted_at.desc()).limit(limit)).all()
    )


def toggle_entry_type(db: Session, entry_id: int) -> LedgerEntry | None:
    entry = db.get(LedgerEntry, entry_id)
    if not entry:
        return None
    cycle = {"expense": "income", "income": "repayment", "repayment": "expense"}
    entry.tx_type = cycle.get(entry.tx_type, "expense")
    entry.manual_override = True
    db.commit()
    db.refresh(entry)
    return entry


def sync_whitelisted_transactions(
    db: Session, start_date: date, end_date: date, account_name: str | None = None
) -> SyncResult:
    """Fetch and parse emails ONLY from whitelisted senders (optionally filtered to one bank account)."""
    active_senders = list_whitelist(db)
    if account_name and account_name.strip().lower() not in ("all", ""):
        target = account_name.strip()
        active_senders = [
            s
            for s in active_senders
            if infer_account_name(s.sender_email, s.display_name, s.display_name).lower() == target.lower()
        ]
    sender_emails = [s.sender_email for s in active_senders]
    if not sender_emails:
        return SyncResult(
            scanned_emails=0,
            inserted_count=0,
            skipped_duplicates=0,
            unparsed_count=0,
            whitelisted_senders_used=[],
            mode=settings.email_mode,
        )

    provider = _resolve_email_provider(db)
    emails = provider.fetch_messages(
        start_date=start_date,
        end_date=end_date,
        sender_emails=sender_emails,
        headers_only=False,
    )

    existing_hashes = set(db.scalars(select(LedgerEntry.idempotency_hash)).all())
    for dl in db.scalars(select(DeletionLog)).all():
        snap = dl.snapshot_json if isinstance(dl.snapshot_json, dict) else {}
        if snap.get("idempotency_hash"):
            existing_hashes.add(snap["idempotency_hash"])
    holder_names = _get_holder_names(db)
    cat_rules = list(db.scalars(select(CategoryRule).order_by(CategoryRule.id.asc())).all())
    inserted = 0
    skipped_dups = 0
    unparsed = 0

    from app.modules.wealth import category_model

    model_state = category_model.get_model_state(db)
    model_json = model_state.model_json if model_state and model_state.model_json else None

    def _tng_password_candidates(raw_pw: str | None) -> list[str | None]:
        cleaned = (raw_pw or "").strip()
        if not cleaned:
            return [None]
        candidates: list[str | None] = [cleaned]
        digits = re.sub(r"\D", "", cleaned)
        if digits and digits not in candidates:
            candidates.append(digits)
        for prefix in ("60", "0"):
            if digits.startswith(prefix) and len(digits) > len(prefix):
                trimmed = digits[len(prefix) :]
                if trimmed not in candidates:
                    candidates.append(trimmed)
        return candidates

    settings_row = db.scalar(select(AppSettings))
    tng_pw_candidates = _tng_password_candidates(settings_row.tng_pdf_password if settings_row else "")

    for raw in emails:
        handled_attachment = False
        for att in getattr(raw, "attachments", None) or []:
            fname_low = (att.filename or "").lower()
            if not fname_low.endswith((".pdf", ".csv")):
                continue
            sender_low = (raw.sender_email or "").lower()
            subj_low = (raw.subject or "").lower()
            is_tng_stmt = (
                "tngdigital.com.my" in sender_low
                or "tng" in fname_low
                or "touch 'n go" in (raw.sender_name or "").lower()
                or ("transaction history" in subj_low and "tng" in (raw.body or "").lower())
            )
            if not is_tng_stmt:
                continue
            for pw_try in tng_pw_candidates:
                try:
                    stmt_res = backfill_from_statement(db, att.filename, att.data, "Touch 'n Go", pdf_password=pw_try)
                    inserted += stmt_res.inserted_count
                    skipped_dups += stmt_res.skipped_duplicates
                    handled_attachment = True
                    break
                except PdfLockedError:
                    continue
                except Exception:
                    break

        parsed_list = parse_transaction_emails(raw)
        if not parsed_list:
            if not handled_attachment:
                unparsed += 1
            continue
        for parsed in parsed_list:
            if parsed.idempotency_hash in existing_hashes:
                skipped_dups += 1
                continue

            existing_hashes.add(parsed.idempotency_hash)
            acct = infer_account_name(parsed.sender_email, parsed.subject, parsed.merchant)
            self_tx = is_self_transfer_match(parsed.merchant, parsed.subject, holder_names)
            matched_rule = match_category_rule_with_flow(
                f"{parsed.merchant} {parsed.subject} {parsed.sender_email}",
                cat_rules,
                occurred_at=raw.received_at,
                amount_myr=parsed.amount_myr,
                tx_type=parsed.tx_type,
                account_name=acct,
            )
            cat = matched_rule[0] if matched_rule else parsed.category
            tx_type = matched_rule[1] if (matched_rule and matched_rule[1] in ("income", "repayment", "expense")) else parsed.tx_type
            if not matched_rule and model_json and not self_tx and cat in ("General", "Bank / Retail"):
                pred_cat, _conf = category_model.predict_category(
                    model_json,
                    parsed.merchant,
                    parsed.subject,
                    raw.sender_email,
                    acct,
                    tx_type,
                    amount_myr=parsed.amount_myr,
                    occurred_at=raw.received_at,
                )
                if pred_cat:
                    cat = pred_cat
            db.add(
                LedgerEntry(
                    idempotency_hash=parsed.idempotency_hash,
                    sender_email=parsed.sender_email,
                    merchant=parsed.merchant,
                    category=cat,
                    tx_type=tx_type,
                    amount_myr=parsed.amount_myr,
                    occurred_at=raw.received_at,
                    subject=parsed.subject,
                    account_name=acct,
                    is_self_transfer=self_tx,
                    is_claimable=(cat.lower() == "business claim"),
                    raw_payload=parsed.raw_payload,
                )
            )
            inserted += 1

    db.commit()
    reconcile_ledger_entries(db)
    return SyncResult(
        scanned_emails=len(emails),
        inserted_count=inserted,
        skipped_duplicates=skipped_dups,
        unparsed_count=unparsed,
        whitelisted_senders_used=sender_emails,
        mode=settings.email_mode,
    )


def backfill_from_statement(
    db: Session,
    filename: str,
    file_bytes: bytes,
    bank_name: str,
    pdf_password: str | None = None,
) -> StatementBackfillResult:
    """Parse a CSV or PDF bank statement and idempotently backfill LedgerEntry rows."""
    txs = parse_statement_file(filename, file_bytes, bank_name, pdf_password=pdf_password)
    existing_hashes = set(db.scalars(select(LedgerEntry.idempotency_hash)).all())
    for dl in db.scalars(select(DeletionLog)).all():
        snap = dl.snapshot_json if isinstance(dl.snapshot_json, dict) else {}
        if snap.get("idempotency_hash"):
            existing_hashes.add(snap["idempotency_hash"])
    holder_names = _get_holder_names(db)
    cat_rules = list(db.scalars(select(CategoryRule).order_by(CategoryRule.id.asc())).all())
    inserted = 0
    skipped = 0

    for tx in txs:
        if tx.idempotency_hash in existing_hashes:
            skipped += 1
            continue
        existing_hashes.add(tx.idempotency_hash)
        subj = f"[{bank_name} Statement] {tx.description}"
        self_tx = is_self_transfer_match(tx.merchant, subj, holder_names)
        matched_rule = match_category_rule_with_flow(
            f"{tx.merchant} {subj}",
            cat_rules,
            occurred_at=tx.occurred_at,
            amount_myr=tx.amount_myr,
            tx_type=tx.tx_type,
            account_name=bank_name,
        )
        cat = matched_rule[0] if matched_rule else tx.category
        tx_type = matched_rule[1] if (matched_rule and matched_rule[1] in ("income", "repayment", "expense")) else tx.tx_type
        db.add(
            LedgerEntry(
                idempotency_hash=tx.idempotency_hash,
                sender_email=f"statement@{bank_name.lower().replace(' ', '')}",
                merchant=tx.merchant,
                category=cat,
                tx_type=tx_type,
                amount_myr=tx.amount_myr,
                occurred_at=tx.occurred_at,
                subject=subj,
                account_name=bank_name,
                is_self_transfer=self_tx,
                raw_payload={"source": "bank_statement", "filename": filename, "bank": bank_name},
            )
        )
        inserted += 1

    db.commit()
    return StatementBackfillResult(
        filename=filename,
        bank_name=bank_name,
        parsed_rows=len(txs),
        inserted_count=inserted,
        skipped_duplicates=skipped,
    )


def add_manual_entry(db: Session, req: ManualLedgerRequest) -> LedgerEntry:
    occurred = req.occurred_at if req.occurred_at.tzinfo else req.occurred_at.replace(tzinfo=timezone.utc)
    raw_key = f"manual|{req.merchant}|{req.tx_type}|{req.amount_myr:.2f}|{occurred.isoformat()}"
    idem = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    existing = db.scalar(select(LedgerEntry).where(LedgerEntry.idempotency_hash == idem))
    if existing:
        return existing

    holder_names = _get_holder_names(db)
    subj = req.notes or f"Manual {req.tx_type}: {req.merchant}"
    entry = LedgerEntry(
        idempotency_hash=idem,
        sender_email="manual@local",
        merchant=req.merchant,
        category=req.category,
        tx_type=req.tx_type,
        amount_myr=round(req.amount_myr, 2),
        occurred_at=occurred,
        subject=subj,
        account_name=req.account_name or "Other / Cash",
        is_self_transfer=is_self_transfer_match(req.merchant, subj, holder_names),
        raw_payload={"source": "manual", "notes": req.notes},
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    if req.category is not None:
        cfg = db.scalar(select(AppSettings))
        if cfg is None or cfg.auto_learn_categories:
            from app.modules.wealth import category_model

            category_model.train_category_model(db)
    if req.recurrence_mode != "one_off":
        upsert_recurring_bill(
            db,
            RecurringBillUpsertRequest(
                name=req.merchant,
                amount_myr=round(req.amount_myr, 2),
                category=req.category or "General",
                due_day=occurred.day,
                frequency=req.recurrence_mode,
                interval_days=req.interval_days,
                match_keywords=req.merchant,
            ),
        )
    return entry


def _suggest_expense_for_repayment(rep: LedgerEntry, expenses: list[LedgerEntry]) -> LedgerEntry | None:
    """Find the highest-confidence expense match for an unlinked repayment within a [-1d, +7d] window."""
    rep_dt = rep.occurred_at if rep.occurred_at.tzinfo else rep.occurred_at.replace(tzinfo=timezone.utc)
    best: tuple[int, float, LedgerEntry] | None = None
    for exp in expenses:
        if exp.id == rep.id or exp.amount_myr + 0.05 < rep.amount_myr:
            continue
        exp_dt = exp.occurred_at if exp.occurred_at.tzinfo else exp.occurred_at.replace(tzinfo=timezone.utc)
        delta_hours = (rep_dt - exp_dt).total_seconds() / 3600.0
        if delta_hours < -24 or delta_hours > 168:
            continue
        score = 0
        # Exact 1/N split match (N=1..6)
        for n in range(1, 7):
            if abs((exp.amount_myr / n) - rep.amount_myr) <= 0.25:
                score += 60 if n > 1 else 50
                break
        if score == 0:
            continue
        if 0 <= delta_hours <= 48:
            score += 25
        elif -24 <= delta_hours < 0:
            score += 10
        if exp.category in {"Food & Beverage", "Shopping & Retail", "Travel & Stay", "Entertainment"}:
            score += 10
        cand = (score, -abs(delta_hours), exp)
        if best is None or cand[:2] > best[:2]:
            best = cand
    return best[2] if best else None


def _enrich_linked_fields(all_rows: list[LedgerEntry], target_rows: list[LedgerEntry]) -> None:
    """Attach computed link, split, merchant normalization, and Gmail thread fields onto LedgerEntry objects."""
    by_id = {r.id: r for r in all_rows}
    repaid_map: dict[int, float] = defaultdict(float)
    candidate_expenses: list[LedgerEntry] = []
    for r in all_rows:
        if r.linked_entry_id and not r.is_self_transfer:
            repaid_map[r.linked_entry_id] += r.amount_myr
        if r.tx_type == "expense" and not r.is_self_transfer:
            candidate_expenses.append(r)

    for r in target_rows:
        payload = r.raw_payload if isinstance(r.raw_payload, dict) else {}
        repaid = round(repaid_map.get(r.id, 0.0), 2)
        r.display_merchant = normalize_merchant_display(r.merchant)
        r.linked_repaid_myr = repaid
        r.net_amount_myr = max(0.0, round(r.amount_myr - repaid, 2)) if (r.tx_type == "expense" and repaid > 0) else None
        parent = by_id.get(r.linked_entry_id) if r.linked_entry_id else None
        r.linked_merchant = normalize_merchant_display(parent.merchant) if parent else None
        r.gmail_thread_id = payload.get("gmail_thread_id")
        r.receipt_items = payload.get("receipt_items") if isinstance(payload.get("receipt_items"), list) else []
        r.added_to_kitchen = payload.get("added_to_kitchen") if isinstance(payload.get("added_to_kitchen"), list) else []

        raw_split = payload.get("split_count")
        split_count = int(raw_split) if isinstance(raw_split, (int, float)) and int(raw_split) >= 2 else None
        r.split_count = split_count
        if split_count and r.tx_type == "expense" and not r.is_self_transfer:
            expected_repaid = round(r.amount_myr * (split_count - 1) / split_count, 2)
            r.owed_remaining_myr = max(0.0, round(expected_repaid - repaid, 2))
        else:
            r.owed_remaining_myr = None

        if r.tx_type == "repayment" and not r.is_self_transfer and not r.linked_entry_id:
            sug = _suggest_expense_for_repayment(r, candidate_expenses)
            r.suggested_link_entry_id = sug.id if sug else None
            r.suggested_link_merchant = normalize_merchant_display(sug.merchant) if sug else None
            r.suggested_link_amount_myr = sug.amount_myr if sug else None
        else:
            r.suggested_link_entry_id = None
            r.suggested_link_merchant = None
            r.suggested_link_amount_myr = None


def resolve_entry_email_url(db: Session, entry_id: int) -> str | None:
    """Return a direct Gmail thread URL for an entry, resolving & caching X-GM-THRID on demand if needed."""
    entry = db.get(LedgerEntry, entry_id)
    if not entry:
        return None
    payload = dict(entry.raw_payload) if isinstance(entry.raw_payload, dict) else {}
    if entry.sender_email.lower().startswith("statement@") or payload.get("source") == "bank_statement":
        return None
    thrid = payload.get("gmail_thread_id")
    msg_id = str(payload.get("message_id") or "").strip()

    if not thrid and msg_id and settings.email_mode.lower() == "imap" and "gmail" in settings.imap_host.lower():
        try:
            provider = ImapEmailProvider(
                host=settings.imap_host,
                port=settings.imap_port,
                user=settings.imap_user,
                app_password=settings.imap_app_password,
            )
            thrid = provider.resolve_gmail_thread_id(msg_id)
            if thrid:
                payload["gmail_thread_id"] = thrid
                entry.raw_payload = payload
                db.commit()
        except Exception:
            thrid = None

    if thrid:
        return f"https://mail.google.com/mail/u/0/#all/{thrid}"
    if msg_id and "@" in msg_id:
        return f"https://mail.google.com/mail/u/0/#search/{quote(f'rfc822msgid:{msg_id}')}"

    day_before = (entry.occurred_at.date() - timedelta(days=1)).strftime("%Y/%m/%d")
    day_after = (entry.occurred_at.date() + timedelta(days=2)).strftime("%Y/%m/%d")
    parts = [f"from:{entry.sender_email}", f"after:{day_before}", f"before:{day_after}"]
    if entry.subject and not entry.subject.startswith("["):
        parts.append(f'"{entry.subject.replace(chr(34), "")}"')
    return f"https://mail.google.com/mail/u/0/#search/{quote(' '.join(parts))}"


def list_entries(
    db: Session, start_date: date | None = None, end_date: date | None = None, month: str | None = None
) -> list[LedgerEntry]:
    stmt = select(LedgerEntry).order_by(LedgerEntry.occurred_at.desc())
    all_rows = list(db.scalars(stmt).all())
    rows = all_rows
    if month:
        rows = [r for r in rows if r.occurred_at.date().isoformat().startswith(month)]
    if start_date:
        rows = [r for r in rows if r.occurred_at.date() >= start_date]
    if end_date:
        rows = [r for r in rows if r.occurred_at.date() <= end_date]
    _enrich_linked_fields(all_rows, rows)
    return rows


def link_repayments(
    db: Session, expense_id: int, repayment_ids: list[int], split_count: int | None = None
) -> LedgerEntry | None:
    """Link one or more repayment entries to an expense entry so summary uses net share."""
    expense = db.get(LedgerEntry, expense_id)
    if not expense:
        return None
    target_set = {rid for rid in repayment_ids if rid != expense_id}

    existing_linked = list(db.scalars(select(LedgerEntry).where(LedgerEntry.linked_entry_id == expense_id)).all())
    for row in existing_linked:
        if row.id not in target_set:
            row.linked_entry_id = None

    if target_set:
        to_link = list(db.scalars(select(LedgerEntry).where(LedgerEntry.id.in_(target_set))).all())
        for rep in to_link:
            rep.linked_entry_id = expense_id

    if split_count is not None:
        payload = dict(expense.raw_payload) if isinstance(expense.raw_payload, dict) else {}
        if split_count <= 1:
            payload.pop("split_count", None)
        else:
            payload["split_count"] = int(split_count)
        expense.raw_payload = payload

    db.commit()
    all_rows = list(db.scalars(select(LedgerEntry)).all())
    _enrich_linked_fields(all_rows, [expense])
    return expense


def accept_suggested_repayment_link(db: Session, repayment_id: int) -> LedgerEntry | None:
    """Link an unlinked repayment to its top suggested expense match."""
    rep = db.get(LedgerEntry, repayment_id)
    if not rep:
        return None
    all_rows = list(db.scalars(select(LedgerEntry)).all())
    _enrich_linked_fields(all_rows, [rep])
    if not rep.suggested_link_entry_id:
        return rep
    rep.linked_entry_id = rep.suggested_link_entry_id
    db.commit()
    all_rows = list(db.scalars(select(LedgerEntry)).all())
    _enrich_linked_fields(all_rows, [rep])
    return rep


def unlink_repayment(db: Session, repayment_id: int) -> LedgerEntry | None:
    """Unlink a repayment entry from its parent expense."""
    rep = db.get(LedgerEntry, repayment_id)
    if not rep:
        return None
    rep.linked_entry_id = None
    db.commit()
    db.refresh(rep)
    all_rows = list(db.scalars(select(LedgerEntry)).all())
    _enrich_linked_fields(all_rows, [rep])
    return rep


def split_entry(db: Session, entry_id: int, parts: list) -> list[LedgerEntry] | None:
    """Split one LedgerEntry into multiple independent entries that can each be categorized and linked."""
    import uuid

    entry = db.get(LedgerEntry, entry_id)
    if not entry:
        return None
    if len(parts) < 2:
        raise ValueError("At least 2 split parts are required.")

    total_parts = round(sum(float(p.amount_myr) for p in parts), 2)
    if abs(total_parts - round(entry.amount_myr, 2)) > 0.02:
        raise ValueError(
            f"Split amounts (RM {total_parts:.2f}) must equal original transaction amount (RM {entry.amount_myr:.2f})."
        )

    orig_amount = entry.amount_myr
    orig_payload = dict(entry.raw_payload) if isinstance(entry.raw_payload, dict) else {}

    p0 = parts[0]
    entry.amount_myr = round(float(p0.amount_myr), 2)
    if p0.merchant and p0.merchant.strip():
        entry.merchant = p0.merchant.strip()
    if p0.category and p0.category.strip():
        entry.category = p0.category.strip()
    if p0.tx_type:
        entry.tx_type = p0.tx_type
    if p0.linked_entry_id is not None and p0.linked_entry_id != entry.id:
        entry.linked_entry_id = p0.linked_entry_id
    entry.manual_override = True
    entry.raw_payload = {**orig_payload, "split_from_amount": orig_amount, "split_part": 1}

    created: list[LedgerEntry] = [entry]
    for idx, part in enumerate(parts[1:], start=2):
        child_hash = f"{entry.idempotency_hash}::split::{idx}::{uuid.uuid4().hex[:8]}"
        child = LedgerEntry(
            idempotency_hash=child_hash,
            sender_email=entry.sender_email,
            subject=entry.subject,
            merchant=(part.merchant.strip() if part.merchant and part.merchant.strip() else f"{entry.merchant} (Split {idx})"),
            category=(part.category.strip() if part.category and part.category.strip() else entry.category),
            tx_type=(part.tx_type or entry.tx_type),
            amount_myr=round(float(part.amount_myr), 2),
            account_name=entry.account_name,
            is_self_transfer=entry.is_self_transfer,
            is_claimable=entry.is_claimable,
            linked_entry_id=(part.linked_entry_id if part.linked_entry_id != entry.id else None),
            manual_override=True,
            raw_payload={**orig_payload, "split_from_amount": orig_amount, "split_parent_id": entry.id, "split_part": idx},
            occurred_at=entry.occurred_at,
        )
        db.add(child)
        created.append(child)

    db.commit()
    for row in created:
        db.refresh(row)
    all_rows = list(db.scalars(select(LedgerEntry)).all())
    _enrich_linked_fields(all_rows, created)
    return created


def seed_default_bank_accounts(db: Session) -> None:
    """Seed the user's primary bank & e-wallet accounts if none are configured yet."""
    if db.scalar(select(BankAccount.id).limit(1)) is not None:
        return
    for name in ("Touch 'n Go", "Ryt Bank", "CIMB", "Public Bank"):
        db.add(BankAccount(name=name, opening_balance_myr=0.0))
    db.commit()


_DEFAULT_CATEGORY_RULES = (
    ("Allowance", "allowance, pocket money, salary, gaji, stipend, parents", "income"),
    ("Food & Beverage", "mixue, mcdonald, kfc, starbucks, zus, chagee, tealive, kopitiam, mamak, cafe, restaurant, bakery, food", "auto"),
    ("Groceries & Mart", "jaya grocer, nsk, aeon, lotus, speedmart, kk mart, familymart, 7-eleven, mynews, grocer", "auto"),
    ("Shopping & Retail", "mr diy, mr.diy, shopee, lazada, pinduoduo, uniqlo, watsons, guardian, decathlon", "auto"),
    ("Subscriptions & Digital", "google*one, google play, apple, spotify, netflix, youtube, chatgpt, openai, icloud", "auto"),
    ("Transport", "grab, bolt, rapidkl, mrt, lrt, ktm, redbus", "auto"),
    ("Fuel & Toll", "setel, petronas, shell, caltex, bhp, fuel, rfid, toll", "auto"),
    ("Parking & E-Wallet", "parking, paydirect, sunway, mid valley, pavilion, trx", "auto"),
    ("P2P Transfer In", "receive from wallet, duitnow_recei", "repayment"),
    ("P2P Transfer Out", "transfer to wallet, duitnow_trans", "expense"),
    ("Bank / Retail", "maybank, cimb, ryt, public bank, duitnow", "auto"),
)


def seed_default_category_rules(db: Session) -> None:
    """Seed starter category keyword rules if none exist yet, and migrate legacy P2P Transfers into In/Out."""
    if db.scalar(select(CategoryRule.id).limit(1)) is None:
        for name, keywords, flow_type in _DEFAULT_CATEGORY_RULES:
            db.add(CategoryRule(name=name, keywords=keywords, flow_type=flow_type))
        db.commit()
        return

    legacy_p2p = db.scalar(select(CategoryRule).where(CategoryRule.name == "P2P Transfers"))
    has_in = db.scalar(select(CategoryRule).where(CategoryRule.name == "P2P Transfer In"))
    has_out = db.scalar(select(CategoryRule).where(CategoryRule.name == "P2P Transfer Out"))
    changed = False
    if legacy_p2p:
        db.delete(legacy_p2p)
        changed = True
    if not has_in:
        db.add(CategoryRule(name="P2P Transfer In", keywords="receive from wallet, duitnow_recei", flow_type="repayment"))
        changed = True
    if not has_out:
        db.add(CategoryRule(name="P2P Transfer Out", keywords="transfer to wallet, duitnow_trans", flow_type="expense"))
        changed = True
    if changed:
        db.commit()


def list_category_rules(db: Session) -> list[CategoryRule]:
    return list(db.scalars(select(CategoryRule).order_by(CategoryRule.id.asc())).all())


def upsert_category_rule(db: Session, req: CategoryRuleUpsertRequest) -> CategoryRule:
    clean_name = req.name.strip()
    clean_kw = ", ".join(k.strip() for k in req.keywords.split(",") if k.strip())
    clean_color = (req.color or "").strip().upper()
    clean_acct = (req.account_filter or "").strip()
    existing = db.scalar(select(CategoryRule).where(CategoryRule.name == clean_name))
    if existing:
        existing.keywords = clean_kw
        existing.flow_type = req.flow_type
        existing.monthly_budget_myr = req.monthly_budget_myr
        existing.color = clean_color
        existing.day_min = req.day_min
        existing.day_max = req.day_max
        existing.amount_min = req.amount_min
        existing.amount_max = req.amount_max
        existing.tx_direction = req.tx_direction
        existing.account_filter = clean_acct
    else:
        existing = CategoryRule(
            name=clean_name,
            keywords=clean_kw,
            flow_type=req.flow_type,
            monthly_budget_myr=req.monthly_budget_myr,
            color=clean_color,
            day_min=req.day_min,
            day_max=req.day_max,
            amount_min=req.amount_min,
            amount_max=req.amount_max,
            tx_direction=req.tx_direction,
            account_filter=clean_acct,
        )
        db.add(existing)
    db.commit()
    db.refresh(existing)
    reconcile_ledger_entries(db)
    return existing


def delete_category_rule(db: Session, rule_id: int) -> bool:
    row = db.get(CategoryRule, rule_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    reconcile_ledger_entries(db)
    return True


def list_conflict_groups(db: Session, window_minutes: int = 5) -> list[ConflictGroupOut]:
    """Detect clusters of same account, tx_type, amount, and merchant within `window_minutes`."""
    rows = list(
        db.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.conflict_resolved.is_(False))
            .order_by(LedgerEntry.occurred_at.asc(), LedgerEntry.id.asc())
        ).all()
    )
    buckets: dict[tuple[str, str, float, str], list[LedgerEntry]] = defaultdict(list)
    for r in rows:
        key = (r.account_name, r.tx_type, round(r.amount_myr, 2), r.merchant.strip().lower())
        buckets[key].append(r)

    max_gap_sec = max(1, window_minutes) * 60
    groups: list[ConflictGroupOut] = []
    for (acct, tx_type, amt, _merch_key), items in buckets.items():
        if len(items) < 2:
            continue
        cluster: list[LedgerEntry] = [items[0]]
        for item in items[1:]:
            gap = abs((item.occurred_at - cluster[-1].occurred_at).total_seconds())
            if gap <= max_gap_sec:
                cluster.append(item)
            else:
                if len(cluster) >= 2:
                    groups.append(
                        ConflictGroupOut(
                            group_key=f"{acct}|{tx_type}|{amt:.2f}|{cluster[0].id}",
                            account_name=acct,
                            merchant=cluster[0].merchant,
                            tx_type=tx_type,
                            amount_myr=amt,
                            entries=[LedgerEntryOut.model_validate(e) for e in cluster],
                        )
                    )
                cluster = [item]
        if len(cluster) >= 2:
            groups.append(
                ConflictGroupOut(
                    group_key=f"{acct}|{tx_type}|{amt:.2f}|{cluster[0].id}",
                    account_name=acct,
                    merchant=cluster[0].merchant,
                    tx_type=tx_type,
                    amount_myr=amt,
                    entries=[LedgerEntryOut.model_validate(e) for e in cluster],
                )
            )
    groups.sort(key=lambda g: g.entries[0].occurred_at, reverse=True)
    return groups


def resolve_conflict_group(db: Session, req: ResolveConflictRequest) -> dict[str, int]:
    """Delete confirmed duplicate entries and mark kept entries as resolved."""
    deleted = 0
    kept = 0
    for entry_id in req.delete_ids:
        row = db.get(LedgerEntry, entry_id)
        if row:
            db.add(
                DeletionLog(
                    original_entry_id=row.id,
                    merchant=row.merchant,
                    category=row.category,
                    account_name=row.account_name,
                    tx_type=row.tx_type,
                    amount_myr=row.amount_myr,
                    occurred_at=row.occurred_at,
                    remark="Resolved duplicate",
                    snapshot_json={
                        "idempotency_hash": row.idempotency_hash,
                        "sender_email": row.sender_email,
                        "subject": row.subject,
                        "is_self_transfer": row.is_self_transfer,
                        "is_claimable": row.is_claimable,
                        "remark": row.remark,
                        "raw_payload": row.raw_payload or {},
                    },
                )
            )
            db.delete(row)
            deleted += 1
    for entry_id in req.keep_ids:
        row = db.get(LedgerEntry, entry_id)
        if row:
            row.conflict_resolved = True
            kept += 1
    db.commit()
    return {"kept": kept, "deleted": deleted}


_BRAND_NOISE = {
    "cardissuance",
    "payment",
    "tngow3my1",
    "duitnow",
    "transfer",
    "pos",
    "debit",
    "card",
    "void",
    "reversal",
    "refund",
}


def _brand_key(merchant: str) -> str:
    low = (merchant or "").lower()
    for brand in ("grab", "setel", "shopee", "lazada", "foodpanda", "google"):
        if re.search(rf"\b{brand}\b|(?<![a-z]){brand}\*", low):
            return brand
    tokens = [
        t
        for t in re.findall(r"[a-z0-9]{3,}", low)
        if t not in _BRAND_NOISE and not t.isdigit()
    ]
    return tokens[0] if tokens else ""


def reconcile_ledger_entries(db: Session) -> dict[str, int]:
    """Clean up internal TNG pocket transfers, fix legacy misclassified directions, and re-tag self-transfers & categories."""
    holder_names = _get_holder_names(db)
    cat_rules = list_category_rules(db)
    rows = list(db.scalars(select(LedgerEntry)).all())
    removed = 0
    updated = 0
    surviving: list[LedgerEntry] = []

    for r in rows:
        subj_lower = (r.subject or "").lower()
        merch_lower = (r.merchant or "").lower()
        # Remove paired internal TNG eWallet <-> GO+ pocket transfers
        if r.sender_email == "statement@touch'ngo" and "via ewallet to go+" in f"{merch_lower} {subj_lower}":
            db.delete(r)
            removed += 1
            continue

        snippet = r.raw_payload.get("snippet", "") if isinstance(r.raw_payload, dict) else ""
        snippet_lower = snippet.lower()
        # Remove failed / reminder alert emails that aren't actual completed transactions
        if any(
            sig in f"{subj_lower} {snippet_lower}"
            for sig in ("hit a bump", "didn't go through", "did not go through", "upcoming scheduled transfer", "reminder: upcoming")
        ):
            db.delete(r)
            removed += 1
            continue

        changed = False
        # Re-extract specific merchant/recipient from stored email snippet if currently generic
        if snippet and (
            r.merchant in ("Ryt Bank", "CIMB", "CIMB Notification", "Public Bank", "PBe Online Banking", "Touch 'n Go")
            or r.merchant.startswith("+603")
            or r.merchant.lower().startswith("the official app stores")
        ):
            fallback_name = (
                (r.raw_payload.get("sender_name") if isinstance(r.raw_payload, dict) else None)
                or r.account_name
                or r.merchant
            )
            if fallback_name.lower().startswith("the official app stores"):
                fallback_name = r.account_name or "CIMB"
            extracted = extract_merchant_from_text(r.subject or "", snippet, fallback_name, r.tx_type == "income")
            if extracted and extracted != r.merchant:
                r.merchant = extracted
                changed = True

        # Fix historical emails whose 4+ digit non-comma amount (e.g. RM1122.58) was truncated to 3 digits (112.00)
        if snippet and isinstance(r.raw_payload, dict) and r.raw_payload.get("message_id"):
            from app.modules.wealth.parser import _extract_tx_amount

            reparsed_amt = _extract_tx_amount(f"{r.subject or ''}\n{snippet}")
            if (
                reparsed_amt
                and reparsed_amt != r.amount_myr
                and r.amount_myr == float(int(r.amount_myr))
                and str(int(reparsed_amt)).startswith(str(int(r.amount_myr)))
                and len(str(int(reparsed_amt))) > len(str(int(r.amount_myr)))
            ):
                r.amount_myr = reparsed_amt
                changed = True
        # Fix historical Google Play receipts saved as income
        if "googleplay-noreply@google.com" in r.sender_email.lower() and r.tx_type != "expense":
            r.tx_type = "expense"
            changed = True

        # Ensure account_name is populated accurately (and migrate legacy merchant-only buckets like Grab/Other)
        inferred_acct = (
            r.raw_payload.get("bank")
            if isinstance(r.raw_payload, dict) and r.raw_payload.get("bank")
            else infer_account_name(r.sender_email, r.subject, r.merchant)
        )
        if not r.account_name or r.account_name in ("Other / Cash", "Grab", "Setel"):
            if inferred_acct != r.account_name:
                r.account_name = inferred_acct
                changed = True

        if not r.manual_override:
            # Fix TNG statement directions & split P2P Transfer In (repayment) vs P2P Transfer Out (expense)
            if r.sender_email == "statement@touch'ngo":
                tng_text = f"{merch_lower} {subj_lower}"
                if "duitnow_recei" in tng_text or "receive from wallet" in tng_text:
                    if r.tx_type == "expense" or r.category in ("P2P Transfers", "P2P Transfer In", "General", "Bank / Retail"):
                        if r.tx_type != "repayment":
                            r.tx_type = "repayment"
                            changed = True
                    if r.category in ("P2P Transfers", "General", "Bank / Retail"):
                        r.category = "P2P Transfer In"
                        changed = True
                elif "transfer to wallet" in tng_text or "duitnow_trans" in tng_text:
                    if r.tx_type != "expense":
                        r.tx_type = "expense"
                        changed = True
                    if r.category in ("P2P Transfers", "General", "Bank / Retail"):
                        r.category = "P2P Transfer Out"
                        changed = True

            self_tx = is_self_transfer_match(r.merchant, r.subject, holder_names)
            if r.is_self_transfer != self_tx:
                r.is_self_transfer = self_tx
                changed = True

            matched_rule = match_category_rule_with_flow(
                f"{r.merchant} {r.subject} {r.sender_email}",
                cat_rules,
                occurred_at=r.occurred_at,
                amount_myr=r.amount_myr,
                tx_type=r.tx_type,
                account_name=r.account_name,
            )
            if matched_rule:
                matched_cat, rule_flow = matched_rule
                if matched_cat != r.category:
                    r.category = matched_cat
                    changed = True
                if rule_flow in ("income", "repayment", "expense") and r.tx_type != rule_flow:
                    r.tx_type = rule_flow
                    changed = True
            elif r.category == "AI Freelance":
                r.category = infer_category_hint(r.sender_email, r.merchant, r.subject)
                changed = True

            # Incoming P2P transfers from another person count as repayment (unless explicitly tagged Allowance/yield)
            if (
                r.tx_type == "income"
                and (not matched_rule or matched_rule[1] == "auto")
                and _is_p2p_transfer_entry(r)
                and not any(kw in f"{merch_lower} {subj_lower}" for kw in ("allowance", "pocket money", "salary", "gaji", "stipend", "interest", "dividend", "cashback", "refund"))
            ):
                r.tx_type = "repayment"
                changed = True

        if changed:
            updated += 1
        surviving.append(r)

    # Deduplicate Unassigned e-receipts (e.g. Grab E-Receipt) when a matching bank/e-wallet
    # card payment (e.g. Ryt Bank "Grab* ...") exists on the same date for the same amount & brand.
    bank_buckets: dict[tuple[date, str, float, str], list[LedgerEntry]] = defaultdict(list)
    unassigned_rows: list[LedgerEntry] = []
    for r in surviving:
        if r.account_name in ("Unassigned", "Other / Cash"):
            unassigned_rows.append(r)
        else:
            bk = _brand_key(r.merchant)
            if bk:
                bank_buckets[(r.occurred_at.date(), r.tx_type, round(r.amount_myr, 2), bk)].append(r)

    for u in unassigned_rows:
        bk = _brand_key(u.merchant)
        if not bk:
            continue
        key = (u.occurred_at.date(), u.tx_type, round(u.amount_myr, 2), bk)
        matches = bank_buckets.get(key)
        if matches:
            matched_bank = matches.pop(0)
            if (
                not matched_bank.manual_override
                and u.category not in ("General", "Bank / Retail", "Transport")
                and matched_bank.category in ("General", "Bank / Retail", "Transport")
            ):
                matched_bank.category = u.category
                updated += 1
            db.delete(u)
            removed += 1

    db.commit()
    return {"removed": removed, "updated": updated}


def _compute_account_balances(db: Session, all_rows: list[LedgerEntry]) -> list[BankAccountBalanceOut]:
    saved_accounts = {
        a.name: a for a in db.scalars(select(BankAccount).order_by(BankAccount.name.asc())).all()
    }
    stats: dict[str, dict[str, float | int]] = defaultdict(lambda: {"in": 0.0, "out": 0.0, "count": 0})
    for r in all_rows:
        acct = r.account_name or infer_account_name(r.sender_email, r.subject, r.merchant)
        stats[acct]["count"] += 1
        if r.tx_type in ("income", "repayment"):
            stats[acct]["in"] += r.amount_myr
        else:
            stats[acct]["out"] += r.amount_myr

    all_names = sorted(set(saved_accounts.keys()) | {k for k in stats.keys() if k not in ("Unassigned", "Other / Cash")})
    balances: list[BankAccountBalanceOut] = []
    for name in all_names:
        saved = saved_accounts.get(name)
        opening = round(saved.opening_balance_myr, 2) if saved else 0.0
        total_in = round(float(stats[name]["in"]), 2)
        total_out = round(float(stats[name]["out"]), 2)
        balances.append(
            BankAccountBalanceOut(
                id=saved.id if saved else None,
                name=name,
                opening_balance_myr=opening,
                total_in_myr=total_in,
                total_out_myr=total_out,
                current_balance_myr=round(opening + total_in - total_out, 2),
                tx_count=int(stats[name]["count"]),
            )
        )
    return balances


def list_bank_accounts(db: Session) -> list[BankAccountBalanceOut]:
    all_rows = list(db.scalars(select(LedgerEntry)).all())
    return _compute_account_balances(db, all_rows)


def upsert_bank_account(db: Session, req: BankAccountUpsertRequest) -> BankAccountBalanceOut:
    clean_name = req.name.strip()
    opening = req.opening_balance_myr
    if req.current_balance_myr is not None:
        all_rows = list(db.scalars(select(LedgerEntry)).all())
        net_flow = sum(
            (r.amount_myr if r.tx_type in ("income", "repayment") else -r.amount_myr)
            for r in all_rows
            if (r.account_name or infer_account_name(r.sender_email, r.subject, r.merchant)) == clean_name
        )
        opening = round(req.current_balance_myr - net_flow, 2)

    existing = db.scalar(select(BankAccount).where(BankAccount.name == clean_name))
    if existing:
        existing.opening_balance_myr = round(opening, 2)
        existing.updated_at = datetime.now(timezone.utc)
    else:
        existing = BankAccount(name=clean_name, opening_balance_myr=round(opening, 2))
        db.add(existing)
    db.commit()
    db.refresh(existing)
    for b in list_bank_accounts(db):
        if b.name == clean_name:
            return b
    return BankAccountBalanceOut(
        id=existing.id,
        name=existing.name,
        opening_balance_myr=existing.opening_balance_myr,
        total_in_myr=0.0,
        total_out_myr=0.0,
        current_balance_myr=existing.opening_balance_myr,
        tx_count=0,
    )


def delete_bank_account(db: Session, account_id: int) -> bool:
    row = db.get(BankAccount, account_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def _is_p2p_transfer_entry(r: LedgerEntry) -> bool:
    if r.is_self_transfer:
        return False
    combined = f"{r.merchant} {r.subject}".lower()
    if any(
        skip in combined
        for skip in (
            "go+ daily earnings",
            "daily interest",
            "duitnow qr",
            "card payment",
            "cimb notification",
            "transaction notification",
        )
    ):
        return False
    if r.category in ("Allowance", "Salary"):
        return False
    if r.category in ("P2P Transfers", "P2P Transfer In", "P2P Transfer Out") or r.tx_type in ("income", "repayment"):
        return True
    return any(
        sig in combined
        for sig in (
            "duitnow_trans",
            "duitnow_recei",
            "transfer to wallet",
            "receive from wallet",
            "transfer settled",
            "scheduled transfer",
            "your money is in",
            "your transfer is successful",
            "duitnow in",
            "duitnow out",
        )
    )


def _extract_transfer_person(merchant: str) -> str:
    name = (merchant or "").strip()
    name = re.sub(r"^(?:DUITNOW_TRANS\s+FERTO|DUITNOW_RECEI\s+VEFROM)\s+\d+\s+", "", name, flags=re.IGNORECASE)
    name = re.sub(r"^(?:Transfer\s+to\s+Wallet|Receive\s+from\s+Wallet\d*)\s*\d*\s+", "", name, flags=re.IGNORECASE)
    name = re.sub(r"^DuitNow\s+(?:In|Out)\s+(?:from|to)\s+", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+Fund\s+Transfer$", "", name, flags=re.IGNORECASE)
    cleaned = " ".join(name.split()).upper()
    return cleaned or "UNKNOWN"


def _compute_transfer_rankings(
    entries: list[LedgerEntry],
) -> tuple[list[TransferCounterpartyStat], list[TransferCounterpartyStat]]:
    out_agg: dict[str, dict] = {}
    in_agg: dict[str, dict] = {}
    for r in entries:
        if not _is_p2p_transfer_entry(r):
            continue
        person = _extract_transfer_person(r.merchant)
        target = in_agg if r.tx_type in ("income", "repayment") else out_agg
        bucket = target.setdefault(person, {"total": 0.0, "count": 0, "accounts": set(), "ids": []})
        bucket["total"] += r.amount_myr
        bucket["count"] += 1
        bucket["accounts"].add(r.account_name)
        bucket["ids"].append(r.id)

    def _to_list(agg: dict[str, dict]) -> list[TransferCounterpartyStat]:
        items = [
            TransferCounterpartyStat(
                name=person,
                total_myr=round(data["total"], 2),
                tx_count=data["count"],
                accounts=sorted(data["accounts"]),
                entry_ids=data["ids"],
            )
            for person, data in agg.items()
        ]
        items.sort(key=lambda x: x.total_myr, reverse=True)
        return items[:15]

    return _to_list(out_agg), _to_list(in_agg)


def get_summary(
    db: Session, start_date: date | None = None, end_date: date | None = None, month: str | None = None
) -> WealthSummary:
    all_rows = list(db.scalars(select(LedgerEntry).order_by(LedgerEntry.occurred_at.asc())).all())
    filtered = [
        r
        for r in all_rows
        if (month is None or r.occurred_at.date().isoformat().startswith(month))
        and (start_date is None or r.occurred_at.date() >= start_date)
        and (end_date is None or r.occurred_at.date() <= end_date)
    ]

    # Exclude internal self-transfers from real Income & Expense totals
    real_filtered = [r for r in filtered if not r.is_self_transfer]
    real_all = [r for r in all_rows if not r.is_self_transfer]

    repaid_by_expense: dict[int, float] = defaultdict(float)
    for r in real_all:
        if r.linked_entry_id:
            repaid_by_expense[r.linked_entry_id] += r.amount_myr

    def _net_expense(r: LedgerEntry) -> float:
        return max(0.0, round(r.amount_myr - repaid_by_expense.get(r.id, 0.0), 2))

    total_income = sum(r.amount_myr for r in real_filtered if r.tx_type == "income")
    total_repayment = sum(r.amount_myr for r in real_filtered if r.tx_type == "repayment")
    total_expense = sum(_net_expense(r) for r in real_filtered if r.tx_type == "expense")

    # Rolling 30-day daily burn rate anchored to end_date (or latest/today)
    anchor_date = end_date or (max((r.occurred_at.date() for r in real_all), default=date.today()))
    window_start = anchor_date - timedelta(days=29)
    rolling_expense = sum(
        _net_expense(r)
        for r in real_all
        if r.tx_type == "expense" and window_start <= r.occurred_at.date() <= anchor_date
    )
    rolling_burn = round(rolling_expense / 30.0, 2)

    if start_date and end_date and end_date >= start_date:
        period_days = (end_date - start_date).days + 1
    elif real_filtered:
        d_min = min(r.occurred_at.date() for r in real_filtered)
        d_max = max(r.occurred_at.date() for r in real_filtered)
        period_days = max(1, (d_max - d_min).days + 1)
    else:
        period_days = 1
    period_burn = round(total_expense / period_days, 2)

    daily_map: dict[str, dict[str, float]] = defaultdict(lambda: {"income": 0.0, "repayment": 0.0, "expense": 0.0})
    monthly_map: dict[str, dict] = defaultdict(
        lambda: {"income": 0.0, "expense": 0.0, "categories": defaultdict(float)}
    )
    category_map: dict[str, float] = defaultdict(float)

    for r in real_filtered:
        day_key = r.occurred_at.date().isoformat()
        month_key = day_key[:7]
        if r.tx_type == "income":
            daily_map[day_key]["income"] += r.amount_myr
            monthly_map[month_key]["income"] += r.amount_myr
        elif r.tx_type == "repayment":
            daily_map[day_key]["repayment"] += r.amount_myr
        elif r.tx_type == "expense":
            eff = _net_expense(r)
            daily_map[day_key]["expense"] += eff
            monthly_map[month_key]["expense"] += eff
            monthly_map[month_key]["categories"][r.category] += eff
            category_map[r.category] += eff

    daily_series = [
        DailyCashFlowPoint(
            date=d,
            income_myr=round(vals["income"], 2),
            repayment_myr=round(vals["repayment"], 2),
            expense_myr=round(vals["expense"], 2),
            net_myr=round(vals["income"] - vals["expense"], 2),
        )
        for d, vals in sorted(daily_map.items())
    ]

    monthly_series = [
        MonthlyCategoryPoint(
            month=m,
            income_myr=round(vals["income"], 2),
            expense_myr=round(vals["expense"], 2),
            net_myr=round(vals["income"] - vals["expense"], 2),
            categories={
                k: round(v, 2)
                for k, v in sorted(vals["categories"].items(), key=lambda x: x[1], reverse=True)
            },
        )
        for m, vals in sorted(monthly_map.items())
    ]

    top_out, top_in = _compute_transfer_rankings(filtered)

    target_month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    recurring_bills = _evaluate_recurring_bills(db, all_rows, target_month)
    remaining_fixed = round(sum(b.amount_myr for b in recurring_bills if not b.paid_this_month), 2)

    cat_budgets = {
        r.name: round(float(r.monthly_budget_myr), 2)
        for r in db.scalars(select(CategoryRule)).all()
        if r.monthly_budget_myr is not None and r.monthly_budget_myr > 0
    }
    burn_pace = _compute_burn_pace_ratio(db, total_expense, target_month)

    return WealthSummary(
        total_income_myr=round(total_income, 2),
        total_repayment_myr=round(total_repayment, 2),
        total_expense_myr=round(total_expense, 2),
        net_cash_flow_myr=round(total_income - total_expense, 2),
        rolling_30d_daily_burn_myr=rolling_burn,
        period_daily_burn_myr=period_burn,
        transaction_count=len(filtered),
        daily_series=daily_series,
        monthly_series=monthly_series,
        category_breakdown={k: round(v, 2) for k, v in sorted(category_map.items(), key=lambda x: x[1], reverse=True)},
        account_balances=_compute_account_balances(db, all_rows),
        top_transfers_out=top_out,
        top_transfers_in=top_in,
        recurring_bills=recurring_bills,
        remaining_fixed_bills_myr=remaining_fixed,
        category_budgets=cat_budgets,
        burn_pace_ratio=burn_pace,
    )


def list_recurring_bills(db: Session, month: str | None = None) -> list[RecurringBillOut]:
    all_rows = list(db.scalars(select(LedgerEntry)).all())
    target_month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    return _evaluate_recurring_bills(db, all_rows, target_month)


def upsert_recurring_bill(db: Session, req: RecurringBillUpsertRequest) -> RecurringBillOut:
    clean_name = req.name.strip()
    clean_kw = ", ".join(k.strip() for k in req.match_keywords.split(",") if k.strip())
    existing = db.scalar(select(RecurringBill).where(RecurringBill.name == clean_name))
    if existing:
        existing.amount_myr = round(req.amount_myr, 2)
        existing.category = req.category.strip() or "Subscriptions & Digital"
        existing.due_day = req.due_day
        existing.frequency = req.frequency
        existing.interval_days = req.interval_days
        existing.match_keywords = clean_kw
    else:
        existing = RecurringBill(
            name=clean_name,
            amount_myr=round(req.amount_myr, 2),
            category=req.category.strip() or "Subscriptions & Digital",
            due_day=req.due_day,
            frequency=req.frequency,
            interval_days=req.interval_days,
            match_keywords=clean_kw,
        )
        db.add(existing)
    db.commit()
    db.refresh(existing)
    all_rows = list(db.scalars(select(LedgerEntry)).all())
    target_month = datetime.now(timezone.utc).strftime("%Y-%m")
    matched = _evaluate_recurring_bills(db, all_rows, target_month)
    for b in matched:
        if b.id == existing.id:
            return b
    return RecurringBillOut(
        id=existing.id,
        name=existing.name,
        category=existing.category,
        amount_myr=existing.amount_myr,
        due_day=existing.due_day,
        frequency=existing.frequency,
        interval_days=existing.interval_days,
        match_keywords=existing.match_keywords,
    )


def delete_recurring_bill(db: Session, bill_id: int) -> bool:
    row = db.get(RecurringBill, bill_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def _evaluate_recurring_bills(
    db: Session, all_rows: list[LedgerEntry], target_month: str
) -> list[RecurringBillOut]:
    """Evaluate user-defined recurring bills against expenses in `target_month`."""
    saved = list(db.scalars(select(RecurringBill).order_by(RecurringBill.due_day.asc(), RecurringBill.name.asc())).all())
    if not saved:
        return []

    month_expenses = [
        r
        for r in all_rows
        if not r.is_self_transfer
        and r.tx_type == "expense"
        and r.occurred_at.strftime("%Y-%m") == target_month
    ]

    out: list[RecurringBillOut] = []
    for bill in saved:
        terms = [t.strip().lower() for t in bill.match_keywords.split(",") if t.strip()]
        if not terms:
            terms = [bill.name.strip().lower()]
        paid_amt = 0.0
        for e in month_expenses:
            hay = f"{e.merchant} {normalize_merchant_display(e.merchant)} {e.subject} {e.remark}".lower()
            if any(t in hay for t in terms):
                paid_amt += e.amount_myr
        out.append(
            RecurringBillOut(
                id=bill.id,
                name=bill.name,
                category=bill.category,
                amount_myr=round(bill.amount_myr, 2),
                due_day=bill.due_day,
                frequency=bill.frequency or "monthly",
                interval_days=bill.interval_days or 30,
                match_keywords=bill.match_keywords,
                paid_this_month=paid_amt > 0,
                paid_amount_myr=round(paid_amt, 2),
            )
        )
    return out


def _compute_burn_pace_ratio(db: Session, total_expense: float, target_month: str) -> float | None:
    """Compare % of monthly budget spent vs % of month elapsed (>1.0 means spending ahead of pace)."""
    cfg = db.scalar(select(AppSettings))
    budget = cfg.monthly_budget_myr if (cfg and cfg.monthly_budget_myr > 0) else 3000.0
    now_dt = datetime.now(timezone.utc)
    if target_month != now_dt.strftime("%Y-%m"):
        return None
    days_in_month = calendar.monthrange(now_dt.year, now_dt.month)[1]
    month_progress = max(now_dt.day / days_in_month, 1.0 / days_in_month)
    budget_progress = total_expense / budget
    return round(budget_progress / month_progress, 2)


def export_entries_csv(db: Session, month: str | None = None, claimable_only: bool = False) -> str:
    """Export ledger entries (with net share after linked repayments) as CSV."""
    rows = list_entries(db, month=month)
    if claimable_only:
        rows = [r for r in rows if r.is_claimable]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Date",
            "Display Merchant",
            "Raw Merchant",
            "Category",
            "Type",
            "Account",
            "Gross (MYR)",
            "Linked Repaid (MYR)",
            "Net (MYR)",
            "Claimable",
            "Self-Transfer",
            "Remark",
            "Source",
        ]
    )
    for r in rows:
        net = r.net_amount_myr if r.net_amount_myr is not None else r.amount_myr
        writer.writerow(
            [
                r.occurred_at.strftime("%Y-%m-%d %H:%M"),
                getattr(r, "display_merchant", "") or r.merchant,
                r.merchant,
                r.category,
                r.tx_type,
                r.account_name,
                f"{r.amount_myr:.2f}",
                f"{getattr(r, 'linked_repaid_myr', 0.0):.2f}",
                f"{net:.2f}",
                "Yes" if r.is_claimable else "No",
                "Yes" if r.is_self_transfer else "No",
                r.remark or "",
                r.sender_email,
            ]
        )
    return buf.getvalue()

