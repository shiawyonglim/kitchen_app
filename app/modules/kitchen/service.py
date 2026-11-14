from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.kitchen.models import KitchenItem, NutritionTarget
from app.modules.kitchen.schemas import (
    AddKitchenItemRequest,
    DinnerRecipeOut,
    KitchenItemOut,
    NutritionTargetOut,
)

_SHELF_LIFE_HINTS = {
    "kangkung": ("Produce", 6, 3.0, 25.0),
    "spinach": ("Produce", 5, 3.0, 25.0),
    "chicken": ("Protein", 4, 27.0, 165.0),
    "egg": ("Protein", 21, 6.0, 70.0),
    "eggs": ("Protein", 21, 6.0, 70.0),
    "tuna": ("Pantry", 365, 24.0, 130.0),
    "milk": ("Dairy", 7, 8.0, 120.0),
    "tofu": ("Protein", 7, 10.0, 95.0),
}


def seed_default_kitchen(db: Session) -> None:
    if db.scalar(select(KitchenItem.id).limit(1)) is not None:
        return
    now = datetime.now(timezone.utc)
    defaults = [
        KitchenItem(
            name="Fresh Kangkung",
            category="Produce",
            quantity=1,
            unit="bunch",
            source="NSK Thermal OCR",
            shelf_life_days=6,
            protein_g=3.0,
            calories=25.0,
            purchased_at=now - timedelta(days=5),  # Day 5: triggers expiry alert
        ),
        KitchenItem(
            name="Kampung Eggs",
            category="Protein",
            quantity=10,
            unit="pcs",
            source="Jaya Grocer",
            shelf_life_days=21,
            protein_g=6.0,
            calories=70.0,
            purchased_at=now - timedelta(days=2),
        ),
        KitchenItem(
            name="Canned Tuna in Water",
            category="Pantry",
            quantity=3,
            unit="cans",
            source="Jaya Grocer",
            shelf_life_days=365,
            protein_g=24.0,
            calories=130.0,
            purchased_at=now - timedelta(days=10),
        ),
    ]
    db.add_all(defaults)
    db.commit()


def _to_item_out(item: KitchenItem) -> KitchenItemOut:
    now = datetime.now(timezone.utc)
    purchased = item.purchased_at if item.purchased_at.tzinfo else item.purchased_at.replace(tzinfo=timezone.utc)
    days_elapsed = max(0, (now - purchased).days)
    days_remaining = item.shelf_life_days - days_elapsed
    return KitchenItemOut(
        id=item.id,
        name=item.name,
        category=item.category,
        quantity=item.quantity,
        unit=item.unit,
        source=item.source,
        shelf_life_days=item.shelf_life_days,
        days_elapsed=days_elapsed,
        days_remaining=days_remaining,
        is_expiring_soon=(days_remaining <= 1 and item.quantity > 0),
        protein_g=item.protein_g,
        calories=item.calories,
        purchased_at=purchased,
    )


def list_items(db: Session) -> list[KitchenItemOut]:
    rows = list(db.scalars(select(KitchenItem).order_by(KitchenItem.purchased_at.asc())).all())
    outs = [_to_item_out(r) for r in rows]
    outs.sort(key=lambda x: (not x.is_expiring_soon, x.days_remaining))
    return outs


def add_item(db: Session, req: AddKitchenItemRequest) -> KitchenItemOut:
    purchased = datetime.now(timezone.utc) - timedelta(days=req.days_old)
    item = KitchenItem(
        name=req.name,
        category=req.category,
        quantity=req.quantity,
        unit=req.unit,
        source=req.source,
        shelf_life_days=req.shelf_life_days,
        protein_g=req.protein_g,
        calories=req.calories,
        purchased_at=purchased,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _to_item_out(item)


def adjust_item_quantity(db: Session, item_id: int, delta: float) -> KitchenItemOut | None:
    item = db.get(KitchenItem, item_id)
    if not item:
        return None
    item.quantity = max(0.0, round(item.quantity + delta, 2))
    if item.quantity == 0:
        db.delete(item)
        db.commit()
        return None
    db.commit()
    db.refresh(item)
    return _to_item_out(item)


def consume_item(db: Session, item_id: int) -> bool:
    item = db.get(KitchenItem, item_id)
    if not item:
        return False
    db.delete(item)
    db.commit()
    return True


def ingest_receipt_text(db: Session, source: str, raw_text: str) -> list[KitchenItemOut]:
    """Parse thermal OCR or Jaya Grocer email lines into fridge inventory items."""
    created: list[KitchenItemOut] = []
    for raw_line in raw_text.splitlines():
        line = re.sub(r"RM\s*\d+(?:\.\d{1,2})?", "", raw_line, flags=re.IGNORECASE).strip(" -*•\t")
        if len(line) < 2:
            continue
        qty_match = re.search(r"(\d+)\s*[xX]\s*", line)
        qty = float(qty_match.group(1)) if qty_match else 1.0
        clean_name = re.sub(r"\d+\s*[xX]\s*", "", line).strip().title()
        if not clean_name:
            continue

        lower = clean_name.lower()
        cat, shelf, prot, cals = ("Produce", 7, 2.0, 40.0)
        for kw, meta in _SHELF_LIFE_HINTS.items():
            if kw in lower:
                cat, shelf, prot, cals = meta
                break

        created.append(
            add_item(
                db,
                AddKitchenItemRequest(
                    name=clean_name,
                    category=cat,
                    quantity=qty,
                    unit="pcs",
                    source=source,
                    shelf_life_days=shelf,
                    protein_g=prot,
                    calories=cals,
                ),
            )
        )
    return created


def _get_or_create_target(db: Session, target_date: date | None = None) -> NutritionTarget:
    d = target_date or date.today()
    row = db.scalar(select(NutritionTarget).where(NutritionTarget.target_date == d))
    if not row:
        row = NutritionTarget(target_date=d)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def apply_workout_recovery(
    db: Session, bonus_calories: int, bonus_protein_g: int, workout_note: str
) -> NutritionTargetOut:
    """Direct in-memory modular monolith hook called by the Physical Trainer module."""
    row = _get_or_create_target(db)
    row.bonus_calories += max(0, bonus_calories)
    row.bonus_protein_g += max(0, bonus_protein_g)
    row.recovery_mode = True
    row.last_workout_note = workout_note
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return get_today_target(db)


def get_today_target(db: Session) -> NutritionTargetOut:
    row = _get_or_create_target(db)
    additions: list[str] = []
    if row.recovery_mode:
        additions = ["+2 Kampung Eggs (12g protein)", "+1 Can Tuna in Water (24g protein)"]
    return NutritionTargetOut(
        target_date=row.target_date,
        base_calories=row.base_calories,
        base_protein_g=row.base_protein_g,
        bonus_calories=row.bonus_calories,
        bonus_protein_g=row.bonus_protein_g,
        total_calories=row.base_calories + row.bonus_calories,
        total_protein_g=row.base_protein_g + row.bonus_protein_g,
        recovery_mode=row.recovery_mode,
        last_workout_note=row.last_workout_note,
        recommended_additions=additions,
    )


def synthesize_dinner_recipe(db: Session) -> DinnerRecipeOut:
    """Use expiring fridge items + Trainer recovery macros via local Ollama (RTX 4060) or fast fallback."""
    items = list_items(db)
    target = get_today_target(db)
    expiring = [i.name for i in items if i.is_expiring_soon]
    all_names = [i.name for i in items if i.quantity > 0]

    prompt = (
        f"Create a concise 4-step high-protein Malaysian dinner recipe using these fridge items: "
        f"{', '.join(all_names)}. Prioritize expiring items: {', '.join(expiring) or 'none'}. "
        f"Target protein: {target.total_protein_g}g. Recovery mode: {target.recovery_mode}."
    )

    try:
        with httpx.Client(timeout=0.8) as probe:
            tags_resp = probe.get("http://127.0.0.1:11434/api/tags")
            models = tags_resp.json().get("models", []) if tags_resp.status_code == 200 else []
            model_name = models[0]["name"] if models else None
        if model_name:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(
                    "http://127.0.0.1:11434/api/generate",
                    json={"model": model_name, "prompt": prompt, "stream": False},
                )
                if resp.status_code == 200 and resp.json().get("response"):
                    return DinnerRecipeOut(
                        title=f"RTX 4060 Local LLM ({model_name})",
                        used_expiring_items=expiring,
                        recovery_additions=target.recommended_additions,
                        instructions=resp.json()["response"].strip(),
                        engine="ollama_local",
                    )
    except Exception:
        pass

    exp_str = ", ".join(expiring) if expiring else "available greens"
    rec_str = " + ".join(target.recommended_additions) if target.recovery_mode else "standard portion"
    instructions = (
        f"1. Flash-stir-fry {exp_str} with garlic and a dash of soy sauce (3 mins).\n"
        f"2. Sear/scramble {rec_str} on high heat for fast muscle recovery.\n"
        f"3. Combine over warm rice or eat as a high-protein bowl ({target.total_protein_g}g daily protein target)."
    )
    return DinnerRecipeOut(
        title=f"Zero-Waste Recovery Bowl ({' + '.join(expiring or ['Fridge Mix'])})",
        used_expiring_items=expiring,
        recovery_additions=target.recommended_additions,
        instructions=instructions,
        engine="deterministic_fallback",
    )

