from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.kitchen import taste
from app.modules.kitchen.models import KitchenItem, NutritionTarget, ShoppingListItem
from app.modules.kitchen.schemas import (
    AddKitchenItemRequest,
    CookedMealOut,
    CookedMealRequest,
    DinnerRecipeOut,
    KitchenItemOut,
    NutritionTargetOut,
    ShoppingListCreate,
    ShoppingListItemOut,
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
        is_expiring_soon=(not item.is_staple and days_remaining <= 1 and item.quantity > 0),
        protein_g=item.protein_g,
        calories=item.calories,
        is_staple=item.is_staple,
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
        is_staple=req.is_staple,
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
    """Use expiring fridge items + Trainer recovery macros + Taste Knowledgebase via local Ollama or fallback."""
    items = list_items(db)
    target = get_today_target(db)
    expiring = [i.name for i in items if i.is_expiring_soon]
    all_names = [i.name for i in items if i.quantity > 0]

    analysis = taste.analyze_fridge_taste(db, all_names)
    applied_rule_ids = [r.rule_id for r in analysis.applied_rules[:3]]
    taste_notes = [
        f"{r.title} ({', '.join(r.matched_ingredients)}): {r.rationale}"
        for r in analysis.applied_rules[:3]
    ]
    taste_notes.extend(analysis.missing_balance_hints[:2])
    for pa in analysis.top_pair_affinities[:2]:
        if pa.affinity_score > 0:
            taste_notes.append(f"Proven favorite pair: {pa.ingredient_a} + {pa.ingredient_b} (affinity {pa.affinity_score:+.2f})")

    technique_notes = [
        f"{p.name}: {p.cooking_method or 'flexible'} ({p.texture or 'balanced'}, {p.aroma or 'mild'} aroma)"
        for p in analysis.matched_profiles
        if p.cooking_method or p.texture or p.aroma
    ]

    palate = analysis.palate
    palate_summary = (
        f"sweet={palate.sweet:.2f}, sour={palate.sour:.2f}, salty={palate.salty:.2f}, "
        f"umami={palate.umami:.2f}, spicy={palate.spicy:.2f}"
    )
    rules_prompt = "\n".join(f"- {n}" for n in taste_notes) or "- Balance savory umami with bright acidity."
    tech_prompt = "\n".join(f"- {t}" for t in technique_notes) or "- Use high-heat sear or stir-fry."

    prompt = (
        f"Create a concise 4-step high-protein Malaysian dinner recipe from first principles using these fridge items: "
        f"{', '.join(all_names)}. Prioritize expiring items: {', '.join(expiring) or 'none'}.\n"
        f"Target protein: {target.total_protein_g}g. Recovery mode: {target.recovery_mode}.\n"
        f"Personal palate weights: {palate_summary}.\n"
        f"Ingredient techniques & textures:\n{tech_prompt}\n"
        f"Apply these learned taste pairing rules and explain the flavor balance:\n{rules_prompt}"
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
                        title=f"RTX 4060 Taste-Aware Chef ({model_name})",
                        used_expiring_items=expiring,
                        recovery_additions=target.recommended_additions,
                        instructions=resp.json()["response"].strip(),
                        engine="ollama_local",
                        taste_notes=taste_notes,
                        applied_rule_ids=applied_rule_ids,
                        ingredients_used=all_names,
                    )
    except Exception:
        pass

    exp_str = ", ".join(expiring) if expiring else "available greens"
    rec_str = " + ".join(target.recommended_additions) if target.recovery_mode else "standard portion"
    rule_line = (
        f"2. Flavor balance: {taste_notes[0]}\n"
        if taste_notes
        else "2. Balance savory aromatics with a splash of acid/sweetness.\n"
    )
    instructions = (
        f"1. Flash-stir-fry {exp_str} with garlic on high heat (3 mins).\n"
        f"{rule_line}"
        f"3. Sear/scramble {rec_str} for fast muscle recovery.\n"
        f"4. Combine into a balanced high-protein bowl ({target.total_protein_g}g daily protein target)."
    )
    return DinnerRecipeOut(
        title=f"Taste-Balanced Recovery Bowl ({' + '.join(expiring or ['Fridge Mix'])})",
        used_expiring_items=expiring,
        recovery_additions=target.recommended_additions,
        instructions=instructions,
        engine="deterministic_fallback",
        taste_notes=taste_notes,
        applied_rule_ids=applied_rule_ids,
        ingredients_used=all_names,
    )


def mark_meal_cooked(db: Session, req: CookedMealRequest) -> CookedMealOut:
    """Deduct used non-staple ingredients from fridge inventory after cooking."""
    deducted: list[str] = []
    removed: list[str] = []
    skipped_staples: list[str] = []

    items = list(db.scalars(select(KitchenItem)).all())
    wanted = [n.strip().lower() for n in req.ingredients_used if n.strip()]
    for item in items:
        ilower = item.name.lower()
        if not any(w == ilower or w in ilower or ilower in w for w in wanted):
            continue
        if item.is_staple:
            skipped_staples.append(item.name)
            continue
        updated = adjust_item_quantity(db, item.id, -req.deduct_qty)
        if updated is None:
            removed.append(item.name)
        else:
            deducted.append(f"{item.name} ({updated.quantity} {updated.unit} left)")
    return CookedMealOut(deducted_items=deducted, removed_items=removed, skipped_staples=skipped_staples)


def _to_shopping_out(row: ShoppingListItem) -> ShoppingListItemOut:
    return ShoppingListItemOut(
        id=row.id,
        name=row.name,
        reason=row.reason,
        is_checked=row.is_checked,
        created_at=row.created_at,
    )


def list_shopping_items(db: Session) -> list[ShoppingListItemOut]:
    rows = list(
        db.scalars(
            select(ShoppingListItem).order_by(ShoppingListItem.is_checked.asc(), ShoppingListItem.created_at.desc())
        ).all()
    )
    return [_to_shopping_out(r) for r in rows]


def add_shopping_item(db: Session, req: ShoppingListCreate) -> ShoppingListItemOut:
    row = ShoppingListItem(name=req.name.strip(), reason=req.reason.strip())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_shopping_out(row)


def toggle_shopping_item(db: Session, item_id: int) -> ShoppingListItemOut | None:
    row = db.get(ShoppingListItem, item_id)
    if not row:
        return None
    row.is_checked = not row.is_checked
    db.commit()
    db.refresh(row)
    return _to_shopping_out(row)


def delete_shopping_item(db: Session, item_id: int) -> bool:
    row = db.get(ShoppingListItem, item_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True

