from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import os
import re
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_current_user_id

from app.modules.assets import service as assets_service
from app.modules.assets.schemas import CreateAssetRequest
from app.modules.assistant import ai_planner
from app.modules.assistant.models import AssistantItem
from app.modules.assistant.schemas import (
    AssistantIngestRequest,
    AssistantItemOut,
    AssistantItemUpdate,
    MorningBriefingOut,
)
from app.modules.friction import service as friction_service
from app.modules.friction.schemas import CreateDebtRequest
from app.modules.kitchen import service as kitchen_service
from app.modules.kitchen import taste as kitchen_taste
from app.modules.kitchen.schemas import AddKitchenItemRequest, ShoppingListCreate
from app.modules.settings import service as settings_service
from app.modules.wealth import service as wealth_service
from app.modules.wealth.models import LedgerEntry


def _to_out(row: AssistantItem, whatsapp_reply: str = "") -> AssistantItemOut:
    return AssistantItemOut(
        id=row.id,
        source=row.source,
        sender=row.sender,
        raw_message=row.raw_message,
        category=row.category,
        title=row.title,
        ai_plan=row.ai_plan,
        scheduled_for=row.scheduled_for,
        priority=row.priority,
        status=row.status,
        routed_to=row.routed_to,
        engine=row.engine,
        cost_tier=row.cost_tier,
        attachment_data_url=row.attachment_data_url,
        whatsapp_reply=whatsapp_reply,
        created_at=row.created_at,
    )


def _infer_bank_name(filename: str, caption: str) -> str:
    combined = f"{filename} {caption}".lower()
    if "tng" in combined or "touch" in combined:
        return "Touch 'n Go"
    if "ryt" in combined:
        return "Ryt Bank"
    if "cimb" in combined:
        return "CIMB"
    if "gx" in combined:
        return "GX Bank"
    if "public" in combined:
        return "Public Bank"
    if "maybank" in combined:
        return "Maybank"
    return "Bank Statement"


def _ingest_bank_statement_document(db: Session, req: AssistantIngestRequest) -> list[AssistantItemOut]:
    filename = req.document_filename.strip() or "statement.pdf"
    bank_name = _infer_bank_name(filename, req.raw_message)
    raw_msg = req.raw_message.strip() or f"Forwarded statement: {filename}"
    pdf_pw: str | None = None
    if "touch" in bank_name.lower() or "tng" in filename.lower():
        saved_pw = settings_service.get_settings(db).tng_pdf_password or ""
        digits = re.sub(r"\D+", "", saved_pw)
        if digits.startswith("60") and len(digits) > 10:
            digits = digits[2:]
        elif digits.startswith("0") and len(digits) > 10:
            digits = digits[1:]
        pdf_pw = digits or None

    try:
        file_bytes = base64.b64decode(req.document_base64)
        res = wealth_service.backfill_from_statement(
            db, filename, file_bytes, bank_name, pdf_password=pdf_pw
        )
        title = f"Imported {bank_name} Statement ({filename})"
        ai_plan = (
            f"• Parsed {res.parsed_rows} row(s)\n"
            f"• Inserted +{res.inserted_count} new transaction(s) ({res.skipped_duplicates} duplicate(s) skipped)"
        )
        row = AssistantItem(
            source=req.source.strip() or "whatsapp",
            sender=req.sender.strip() or "Me",
            raw_message=raw_msg,
            category="bank_statement",
            title=title,
            ai_plan=ai_plan,
            scheduled_for="",
            priority="normal",
            status="done",
            routed_to="wealth_ledger",
            engine="statement_parser",
            cost_tier="low_cost",
            attachment_data_url="",
        )
    except Exception as exc:
        title = f"Bank Statement Needs Review ({filename})"
        ai_plan = (
            f"• Could not auto-import: {exc}\n"
            "• Check PDF password in Settings or import in Wealth Ledger."
        )
        row = AssistantItem(
            source=req.source.strip() or "whatsapp",
            sender=req.sender.strip() or "Me",
            raw_message=raw_msg,
            category="bank_statement",
            title=title,
            ai_plan=ai_plan,
            scheduled_for="",
            priority="high",
            status="pending",
            routed_to="",
            engine="statement_parser",
            cost_tier="low_cost",
            attachment_data_url="",
        )

    db.add(row)
    db.commit()
    db.refresh(row)
    model_tag = f"_Reply by: {row.engine} ({row.cost_tier})_"
    return [_to_out(row, whatsapp_reply=f"*{row.title}*\n{row.ai_plan}\n\n{model_tag}")]


def _build_app_context(db: Session) -> dict:
    ctx: dict = {}
    try:
        items = [i for i in kitchen_service.list_items(db) if i.quantity > 0]
        names = [i.name for i in items]
        analysis = kitchen_taste.analyze_fridge_taste(db, names) if names else None
        ctx["kitchen_matrix"] = {
            "items": [
                {"name": i.name, "qty": i.quantity, "unit": i.unit, "expiring": i.is_expiring_soon}
                for i in items[:25]
            ],
            "profiles": [
                {
                    "name": p.name,
                    "method": p.cooking_method,
                    "texture": p.texture,
                    "aroma": p.aroma,
                }
                for p in (analysis.matched_profiles[:10] if analysis else [])
            ],
            "taste_rules": [
                f"{r.title}: {r.rationale}" for r in (analysis.applied_rules[:4] if analysis else [])
            ],
        }
    except Exception:
        ctx["kitchen_matrix"] = {"items": [], "profiles": [], "taste_rules": []}

    try:
        ctx["assets"] = [
            {
                "name": a.name,
                "category": a.category,
                "value_myr": a.total_liquid_value_myr,
            }
            for a in assets_service.list_assets(db)[:12]
        ]
    except Exception:
        ctx["assets"] = []

    try:
        summary = friction_service.get_summary(db)
        ctx["debts"] = [
            {
                "event": d.event_name,
                "party": d.borrower_name,
                "direction": d.direction,
                "remaining_myr": d.remaining_myr,
            }
            for d in summary.unsettled_debts
            if not d.settled
        ][:12]
    except Exception:
        ctx["debts"] = []

    try:
        nw = assets_service.get_net_worth(db)
        ctx["net_worth"] = {
            "liquid_cash_myr": nw.liquid_cash_myr,
            "assets_myr": nw.tracked_assets_value_myr,
            "net_worth_myr": nw.total_net_worth_myr,
        }
    except Exception:
        pass
    return ctx


def _execute_app_action(db: Session, action: dict | None) -> tuple[str, str]:
    if not isinstance(action, dict):
        return "", ""
    act_type = str(action.get("type") or "").strip().lower()
    if act_type == "create_asset":
        try:
            name = str(action.get("name") or "").strip()
            price = float(action.get("purchase_price_myr") or 0.0)
            if not name:
                return "", ""
            asset = assets_service.create_asset(
                db,
                CreateAssetRequest(
                    name=name[:255],
                    search_query=str(action.get("search_query") or f"{name} Malaysia price")[:255],
                    category=str(action.get("category") or "Hardware")[:64],
                    condition=str(action.get("condition") or "Used")[:64],
                    purchase_price_myr=max(0.0, price),
                    quantity=max(1, int(action.get("quantity") or 1)),
                ),
            )
            return "asset_tracker", f"Added to Assets: {asset.name} (RM{asset.purchase_price_myr:,.2f}, {asset.category})."
        except Exception:
            return "", ""

    if act_type == "create_debt":
        try:
            amount = float(action.get("amount_myr") or 0.0)
            borrower = str(action.get("borrower_name") or "Bank").strip()
            if amount <= 0 or not borrower:
                return "", ""
            direction = str(action.get("direction") or "i_owe").strip().lower()
            if direction not in ("i_owe", "owed_to_me"):
                direction = "i_owe"
            int_type = str(action.get("interest_type") or "none").strip().lower()
            if int_type not in ("none", "flat", "reducing"):
                int_type = "none"
            tenure = action.get("tenure_months")
            tenure_int = int(tenure) if tenure and int(tenure) >= 1 else None
            due = action.get("due_day")
            due_int = int(due) if due and 1 <= int(due) <= 31 else None
            monthly = action.get("monthly_installment_myr")
            monthly_val = float(monthly) if monthly and float(monthly) > 0 else None
            debt = friction_service.create_debt(
                db,
                CreateDebtRequest(
                    event_name=str(action.get("event_name") or "Loan")[:255],
                    borrower_name=borrower[:128],
                    amount_myr=amount,
                    direction=direction,
                    monthly_installment_myr=monthly_val,
                    interest_rate_pct=max(0.0, min(100.0, float(action.get("interest_rate_pct") or 0.0))),
                    interest_type=int_type,
                    tenure_months=tenure_int,
                    due_day=due_int,
                ),
            )
            return "debt_tracker", f"Added to Debt Tracker: {debt.event_name} — {debt.borrower_name} (RM{debt.amount_myr:,.2f}, {debt.direction})."
        except Exception:
            return "", ""

    return "", ""


def _ingest_receipt_image_to_kitchen_and_ledger(
    db: Session,
    req: AssistantIngestRequest,
    receipt: dict,
    attachment_data_url: str,
) -> list[AssistantItemOut]:
    merchant = str(receipt.get("merchant") or "Receipt Purchase").strip()[:255]
    raw_items = receipt.get("items") if isinstance(receipt.get("items"), list) else []
    norm_items: list[dict] = []
    added_kitchen: list[str] = []

    for it in raw_items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        qty = max(0.1, float(it.get("qty") or 1.0))
        unit = str(it.get("unit") or "pcs").strip()[:32] or "pcs"
        price = max(0.0, float(it.get("price_myr") or 0.0))
        is_food = bool(it.get("is_food"))
        days = int(it.get("days_until_expiry") or (5 if is_food else 0))
        norm_items.append(
            {
                "name": name,
                "qty": qty,
                "unit": unit,
                "price_myr": round(price, 2),
                "is_food": is_food,
            }
        )
        if is_food:
            try:
                kitchen_service.add_item(
                    db,
                    AddKitchenItemRequest(
                        name=name[:120],
                        quantity=qty,
                        unit=unit,
                        days_until_expiry=max(1, min(365, days)),
                    ),
                )
                added_kitchen.append(name)
            except Exception:
                pass

    total_myr = float(receipt.get("total_myr") or 0.0)
    if total_myr <= 0 and norm_items:
        total_myr = sum(x["price_myr"] for x in norm_items)

    date_str = str(receipt.get("date") or "").strip()
    try:
        occurred_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        occurred_dt = datetime.now(timezone.utc)

    ledger_added = False
    if total_myr > 0:
        img_hash = hashlib.sha256(req.image_base64.encode("utf-8")).hexdigest()[:24]
        idem_hash = hashlib.sha256(f"{get_current_user_id()}:wa_receipt:{img_hash}".encode()).hexdigest()
        existing_ledger = db.scalar(select(LedgerEntry).where(LedgerEntry.idempotency_hash == idem_hash))
        if not existing_ledger:
            category = "Groceries" if added_kitchen else "General"
            entry = LedgerEntry(
                user_id=get_current_user_id(),
                idempotency_hash=idem_hash,
                sender_email="whatsapp_receipt",
                merchant=merchant,
                category=category,
                tx_type="expense",
                amount_myr=round(total_myr, 2),
                occurred_at=occurred_dt,
                subject=f"Receipt · {len(norm_items)} item(s)" if norm_items else "WhatsApp Receipt",
                account_name=str(receipt.get("account_name") or "Other / Cash")[:64],
                raw_payload={
                    "source": "whatsapp_receipt",
                    "receipt_items": norm_items,
                    "added_to_kitchen": added_kitchen,
                },
            )
            db.add(entry)
            ledger_added = True

    lines = [
        f"• Merchant: {merchant} (RM{total_myr:,.2f})",
        f"• Wealth Ledger: {'Logged expense with ' + str(len(norm_items)) + ' itemized product(s)' if ledger_added else 'Already logged (duplicate receipt skipped)'}",
    ]
    if added_kitchen:
        lines.append(f"• Kitchen Matrix ({len(added_kitchen)} added): {', '.join(added_kitchen[:12])}")
    else:
        lines.append("• Kitchen Matrix: No food items detected on this receipt.")

    title = f"{merchant} Receipt (RM{total_myr:,.2f})"
    ai_plan = "\n".join(lines)
    row = AssistantItem(
        source=req.source.strip() or "whatsapp",
        sender=req.sender.strip() or "Me",
        raw_message=req.raw_message.strip() or f"Receipt image: {merchant}",
        category="expense",
        title=title,
        ai_plan=ai_plan,
        scheduled_for="",
        priority="normal",
        status="done",
        routed_to="wealth_ledger+kitchen" if added_kitchen else "wealth_ledger",
        engine="receipt_vision",
        cost_tier="high_cost",
        attachment_data_url=attachment_data_url,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    wa_reply = f"*{row.title}*\n\n{row.ai_plan}\n\n_Reply by: {row.engine} ({row.cost_tier})_"
    return [_to_out(row, whatsapp_reply=wa_reply)]


def ingest_message(db: Session, req: AssistantIngestRequest) -> list[AssistantItemOut]:
    if req.document_base64:
        return _ingest_bank_statement_document(db, req)

    raw_text = req.raw_message.strip()
    attachment_data_url = ""
    if req.image_base64:
        mime = req.image_mime or "image/jpeg"
        attachment_data_url = f"data:{mime};base64,{req.image_base64}"
        if not raw_text.lower().startswith("pay "):
            receipt = ai_planner.extract_receipt_details(req.image_base64, mime)
            if receipt and receipt.get("is_receipt") and (receipt.get("total_myr", 0) > 0 or receipt.get("items")):
                return _ingest_receipt_image_to_kitchen_and_ledger(db, req, receipt, attachment_data_url)
        img_ctx = ai_planner.extract_image_context(req.image_base64, mime)
        if img_ctx:
            raw_text = f"{raw_text}\n[Attached Image/Receipt]: {img_ctx}".strip()
    if not raw_text:
        raw_text = "Forwarded image/receipt"

    recent_rows = list(
        db.scalars(
            select(AssistantItem)
            .where(AssistantItem.status == "pending")
            .order_by(AssistantItem.created_at.desc())
            .limit(5)
        ).all()
    )
    recent_ctx = [
        {"id": r.id, "category": r.category, "title": r.title, "scheduled_for": r.scheduled_for}
        for r in recent_rows
    ]
    app_ctx = _build_app_context(db)
    plans = ai_planner.analyze_messages(
        raw_text,
        sender=req.sender,
        recent_pending=recent_ctx,
        has_image=bool(req.image_base64),
        app_context=app_ctx,
    )
    created_or_updated: list[AssistantItem] = []

    for parsed in plans:
        routed_to = ""
        item_status = "pending"
        ai_plan_text = parsed["ai_plan"]

        if parsed["category"] == "kitchen" and parsed["kitchen_items"]:
            for k_name in parsed["kitchen_items"]:
                kitchen_service.add_shopping_item(
                    db,
                    ShoppingListCreate(name=k_name, reason=f"From {req.source}: {parsed['title']}"),
                )
            routed_to = "kitchen_shopping"

        act_route, act_note = _execute_app_action(db, parsed.get("app_action"))
        if act_route:
            routed_to = act_route
            item_status = "done"
            if act_note and act_note not in ai_plan_text:
                ai_plan_text = f"{act_note}\n{ai_plan_text}".strip()

        merge_id = parsed.get("merge_into_id")
        existing = db.get(AssistantItem, merge_id) if merge_id else None
        if existing and existing.status == "pending":
            existing.raw_message = f"{existing.raw_message}\n---\n{raw_text}"
            existing.category = parsed["category"]
            existing.title = parsed["title"]
            existing.ai_plan = ai_plan_text
            if parsed["scheduled_for"]:
                existing.scheduled_for = parsed["scheduled_for"]
            existing.priority = parsed["priority"]
            if routed_to:
                existing.routed_to = routed_to
                existing.status = item_status
            if attachment_data_url:
                existing.attachment_data_url = attachment_data_url
            existing.engine = parsed["engine"]
            existing.cost_tier = parsed.get("cost_tier", "low_cost")
            created_or_updated.append(existing)
        else:
            row = AssistantItem(
                source=req.source.strip() or "manual",
                sender=req.sender.strip() or "Me",
                raw_message=raw_text,
                category=parsed["category"],
                title=parsed["title"],
                ai_plan=ai_plan_text,
                scheduled_for=parsed["scheduled_for"],
                priority=parsed["priority"],
                status=item_status,
                routed_to=routed_to,
                engine=parsed["engine"],
                cost_tier=parsed.get("cost_tier", "low_cost"),
                attachment_data_url=attachment_data_url,
            )
            db.add(row)
            created_or_updated.append(row)

    db.commit()
    outs: list[AssistantItemOut] = []
    for row in created_or_updated:
        db.refresh(row)
        should_reply = bool(row.ai_plan) and (
            row.category == "question" or row.routed_to in ("asset_tracker", "debt_tracker")
        )
        wa_reply = (
            f"*{row.title}*\n\n{row.ai_plan}\n\n_Reply by: {row.engine} ({row.cost_tier})_"
            if should_reply
            else ""
        )
        outs.append(_to_out(row, whatsapp_reply=wa_reply))
    return outs


def reanalyze_item(db: Session, item_id: int) -> AssistantItemOut | None:
    row = db.get(AssistantItem, item_id)
    if not row:
        return None
    plans = ai_planner.analyze_messages(
        row.raw_message,
        sender=row.sender,
        recent_pending=[],
        has_image=bool(row.attachment_data_url),
        app_context=_build_app_context(db),
    )
    if plans:
        parsed = plans[0]
        row.category = parsed["category"]
        row.title = parsed["title"]
        row.ai_plan = parsed["ai_plan"]
        row.scheduled_for = parsed["scheduled_for"]
        row.priority = parsed["priority"]
        row.engine = parsed["engine"]
        row.cost_tier = parsed.get("cost_tier", row.cost_tier)
        db.commit()
        db.refresh(row)
    return _to_out(row)


def list_items(db: Session, status: str | None = None) -> list[AssistantItemOut]:
    stmt = select(AssistantItem).order_by(AssistantItem.created_at.desc())
    if status:
        stmt = stmt.where(AssistantItem.status == status)
    return [_to_out(r) for r in db.scalars(stmt).all()]


def update_item(db: Session, item_id: int, req: AssistantItemUpdate) -> AssistantItemOut | None:
    row = db.get(AssistantItem, item_id)
    if not row:
        return None
    if req.status is not None:
        row.status = req.status
    if req.scheduled_for is not None:
        row.scheduled_for = req.scheduled_for.strip()
    if req.priority is not None:
        row.priority = req.priority
    db.commit()
    db.refresh(row)
    return _to_out(row)


def delete_item(db: Session, item_id: int) -> bool:
    row = db.get(AssistantItem, item_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


_BRIEFING_CACHE: dict[int, tuple[str, str, str]] = {}


def get_morning_briefing(db: Session, force: bool = False) -> MorningBriefingOut:
    pending = list_items(db, status="pending")
    scheduled = [i for i in pending if i.scheduled_for]
    kitchen_items = kitchen_service.list_items(db)
    expiring = [i.name for i in kitchen_items if i.is_expiring_soon]

    now = datetime.now()
    sig_src = "|".join(
        [now.strftime("%Y-%m-%d")]
        + [f"{i.id}:{i.title}:{i.scheduled_for}" for i in pending]
        + [f"exp:{','.join(expiring)}"]
    )
    signature = hashlib.sha256(sig_src.encode("utf-8")).hexdigest()
    uid = db.info.get("user_id") or get_current_user_id()

    cached = _BRIEFING_CACHE.get(uid)
    if not force and cached and cached[0] == signature:
        focus_plan, engine = cached[1], cached[2]
    else:
        focus_plan, engine = ai_planner.generate_morning_briefing_text(
            pending_titles=[i.title for i in pending],
            scheduled_lines=[f"{i.scheduled_for}: {i.title}" for i in scheduled],
            expiring_kitchen=expiring,
        )
        _BRIEFING_CACHE[uid] = (signature, focus_plan, engine)

    hour = now.hour
    greeting = "Good morning" if hour < 12 else ("Good afternoon" if hour < 18 else "Good evening")
    return MorningBriefingOut(
        date_label=now.strftime("%A, %d %b %Y"),
        greeting=greeting,
        pending_tasks=pending[:15],
        scheduled_items=scheduled[:10],
        expiring_kitchen=expiring,
        ai_focus_plan=focus_plan,
        engine=engine,
    )


def send_briefing_to_whatsapp(db: Session) -> dict:
    """Send the current Daily Focus Plan summary to the user's WhatsApp self-chat via the local bridge."""
    if not settings.whatsapp_bridge_token:
        raise ValueError("WHATSAPP_BRIDGE_TOKEN is not configured.")
    b = get_morning_briefing(db)
    lines = [
        f"*{b.greeting} — Daily Focus Plan*",
        f"_{b.date_label}_",
        "",
        b.ai_focus_plan,
    ]
    if b.scheduled_items:
        lines.append("")
        lines.append("*Scheduled:*")
        for s in b.scheduled_items[:6]:
            lines.append(f"• {s.scheduled_for} — {s.title}")
    if b.expiring_kitchen:
        lines.append("")
        lines.append(f"*Expiring in Fridge:* {', '.join(b.expiring_kitchen)}")

    port = int(os.environ.get("WHATSAPP_BRIDGE_SEND_PORT", "8009"))
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            f"http://127.0.0.1:{port}/send",
            headers={"X-Bridge-Token": settings.whatsapp_bridge_token},
            json={"text": "\n".join(lines)},
        )
    if resp.status_code != 200:
        detail = resp.json().get("detail", resp.text) if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        raise RuntimeError(f"WhatsApp bridge error ({resp.status_code}): {detail}")
    return resp.json()
