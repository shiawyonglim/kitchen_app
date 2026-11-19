from __future__ import annotations

from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.assistant import ai_planner
from app.modules.assistant.models import AssistantItem
from app.modules.assistant.schemas import (
    AssistantIngestRequest,
    AssistantItemOut,
    AssistantItemUpdate,
    MorningBriefingOut,
)
from app.modules.kitchen import service as kitchen_service
from app.modules.kitchen.schemas import ShoppingListCreate


def _to_out(row: AssistantItem) -> AssistantItemOut:
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
        created_at=row.created_at,
    )


def ingest_message(db: Session, req: AssistantIngestRequest) -> list[AssistantItemOut]:
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
    plans = ai_planner.analyze_messages(req.raw_message, sender=req.sender, recent_pending=recent_ctx)
    created_or_updated: list[AssistantItem] = []

    for parsed in plans:
        routed_to = ""
        if parsed["category"] == "kitchen" and parsed["kitchen_items"]:
            for k_name in parsed["kitchen_items"]:
                kitchen_service.add_shopping_item(
                    db,
                    ShoppingListCreate(name=k_name, reason=f"From {req.source}: {parsed['title']}"),
                )
            routed_to = "kitchen_shopping"

        merge_id = parsed.get("merge_into_id")
        existing = db.get(AssistantItem, merge_id) if merge_id else None
        if existing and existing.status == "pending":
            existing.raw_message = f"{existing.raw_message}\n---\n{req.raw_message.strip()}"
            existing.category = parsed["category"]
            existing.title = parsed["title"]
            existing.ai_plan = parsed["ai_plan"]
            if parsed["scheduled_for"]:
                existing.scheduled_for = parsed["scheduled_for"]
            existing.priority = parsed["priority"]
            if routed_to:
                existing.routed_to = routed_to
            existing.engine = parsed["engine"]
            created_or_updated.append(existing)
        else:
            row = AssistantItem(
                source=req.source.strip() or "manual",
                sender=req.sender.strip() or "Me",
                raw_message=req.raw_message.strip(),
                category=parsed["category"],
                title=parsed["title"],
                ai_plan=parsed["ai_plan"],
                scheduled_for=parsed["scheduled_for"],
                priority=parsed["priority"],
                status="pending",
                routed_to=routed_to,
                engine=parsed["engine"],
            )
            db.add(row)
            created_or_updated.append(row)

    db.commit()
    for row in created_or_updated:
        db.refresh(row)
    return [_to_out(row) for row in created_or_updated]


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


def get_morning_briefing(db: Session) -> MorningBriefingOut:
    pending = list_items(db, status="pending")
    scheduled = [i for i in pending if i.scheduled_for]
    kitchen_items = kitchen_service.list_items(db)
    expiring = [i.name for i in kitchen_items if i.is_expiring_soon]

    focus_plan, engine = ai_planner.generate_morning_briefing_text(
        pending_titles=[i.title for i in pending],
        scheduled_lines=[f"{i.scheduled_for}: {i.title}" for i in scheduled],
        expiring_kitchen=expiring,
    )
    now = datetime.now()
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
