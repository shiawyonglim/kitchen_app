"""Taste Knowledgebase & Personal Palate Learning Engine for Kitchen Matrix."""
from __future__ import annotations

import json
from datetime import datetime, timezone
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.kitchen.models import (
    IngredientPairAffinity,
    IngredientTasteProfile,
    PalatePreference,
    TasteFeedback,
    TasteRule,
)
from app.modules.kitchen.schemas import (
    AppliedRuleMatch,
    AutoProfileOut,
    PairAffinityOut,
    PalatePreferenceOut,
    TasteAnalysisOut,
    TasteFeedbackLogOut,
    TasteFeedbackOut,
    TasteFeedbackRequest,
    TasteProfileOut,
    TasteProfileUpsert,
    TasteRuleCreate,
    TasteRuleOut,
)

TASTE_DIMS = ("sweet", "sour", "salty", "bitter", "umami", "spicy", "richness")


def _clamp(val: float, lo: float = 0.05, hi: float = 1.0) -> float:
    return round(max(lo, min(hi, val)), 3)


def _normalize_taste(raw: str) -> str:
    t = raw.strip().lower()
    return t if t in TASTE_DIMS else "umami"


def _to_profile_out(row: IngredientTasteProfile) -> TasteProfileOut:
    return TasteProfileOut(
        id=row.id,
        name=row.name,
        sweet=row.sweet,
        sour=row.sour,
        salty=row.salty,
        bitter=row.bitter,
        umami=row.umami,
        spicy=row.spicy,
        richness=row.richness,
        texture=row.texture,
        aroma=row.aroma,
        cooking_method=row.cooking_method,
        notes=row.notes,
        updated_at=row.updated_at,
    )


def _to_rule_out(row: TasteRule) -> TasteRuleOut:
    return TasteRuleOut(
        id=row.id,
        title=row.title,
        primary_taste=row.primary_taste,
        pair_tastes=row.pair_tastes,
        rationale=row.rationale,
        weight=round(row.weight, 3),
        times_applied=row.times_applied,
        created_at=row.created_at,
    )


def _to_palate_out(row: PalatePreference) -> PalatePreferenceOut:
    return PalatePreferenceOut(
        sweet=round(row.sweet, 3),
        sour=round(row.sour, 3),
        salty=round(row.salty, 3),
        bitter=round(row.bitter, 3),
        umami=round(row.umami, 3),
        spicy=round(row.spicy, 3),
        richness=round(row.richness, 3),
        feedback_count=row.feedback_count,
    )


def _get_or_create_palate(db: Session) -> PalatePreference:
    row = db.scalar(select(PalatePreference).limit(1))
    if not row:
        row = PalatePreference()
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def list_profiles(db: Session) -> list[TasteProfileOut]:
    rows = list(db.scalars(select(IngredientTasteProfile).order_by(IngredientTasteProfile.name.asc())).all())
    return [_to_profile_out(r) for r in rows]


def upsert_profile(db: Session, req: TasteProfileUpsert) -> TasteProfileOut:
    key = req.name.strip().lower()
    row = db.scalar(select(IngredientTasteProfile).where(IngredientTasteProfile.name_key == key))
    if row:
        row.name = req.name.strip()
        row.sweet = req.sweet
        row.sour = req.sour
        row.salty = req.salty
        row.bitter = req.bitter
        row.umami = req.umami
        row.spicy = req.spicy
        row.richness = req.richness
        row.texture = req.texture.strip()
        row.aroma = req.aroma.strip()
        row.cooking_method = req.cooking_method.strip()
        row.notes = req.notes.strip()
        row.updated_at = datetime.now(timezone.utc)
    else:
        row = IngredientTasteProfile(
            name=req.name.strip(),
            name_key=key,
            sweet=req.sweet,
            sour=req.sour,
            salty=req.salty,
            bitter=req.bitter,
            umami=req.umami,
            spicy=req.spicy,
            richness=req.richness,
            texture=req.texture.strip(),
            aroma=req.aroma.strip(),
            cooking_method=req.cooking_method.strip(),
            notes=req.notes.strip(),
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return _to_profile_out(row)


def delete_profile(db: Session, profile_id: int) -> bool:
    row = db.get(IngredientTasteProfile, profile_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def list_rules(db: Session) -> list[TasteRuleOut]:
    rows = list(db.scalars(select(TasteRule).order_by(TasteRule.weight.desc(), TasteRule.id.asc())).all())
    return [_to_rule_out(r) for r in rows]


def create_rule(db: Session, req: TasteRuleCreate) -> TasteRuleOut:
    primary = _normalize_taste(req.primary_taste)
    pairs = ",".join(_normalize_taste(p) for p in req.pair_tastes.split(",") if p.strip()) or "sour"
    row = TasteRule(
        title=req.title.strip(),
        primary_taste=primary,
        pair_tastes=pairs,
        rationale=req.rationale.strip(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_rule_out(row)


def delete_rule(db: Session, rule_id: int) -> bool:
    row = db.get(TasteRule, rule_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def get_palate(db: Session) -> PalatePreferenceOut:
    return _to_palate_out(_get_or_create_palate(db))


def _match_profiles(profiles: list[IngredientTasteProfile], item_names: list[str]) -> list[IngredientTasteProfile]:
    matched: list[IngredientTasteProfile] = []
    seen_ids: set[int] = set()
    lowered_items = [n.strip().lower() for n in item_names if n.strip()]
    for prof in profiles:
        for item_l in lowered_items:
            if prof.name_key == item_l or prof.name_key in item_l or item_l in prof.name_key:
                if prof.id not in seen_ids:
                    seen_ids.add(prof.id)
                    matched.append(prof)
                break
    return matched


def analyze_fridge_taste(db: Session, item_names: list[str]) -> TasteAnalysisOut:
    """Evaluate fridge ingredients against personal taste rules and palate preferences."""
    all_profiles = list(db.scalars(select(IngredientTasteProfile)).all())
    rules = list(db.scalars(select(TasteRule).order_by(TasteRule.weight.desc())).all())
    palate = _get_or_create_palate(db)

    matched = _match_profiles(all_profiles, item_names)
    applied_rules: list[AppliedRuleMatch] = []
    missing_hints: list[str] = []

    for rule in rules:
        primary = rule.primary_taste
        pair_dims = [p.strip() for p in rule.pair_tastes.split(",") if p.strip()]
        primary_ings = [p for p in matched if getattr(p, primary, 0.0) >= 0.3]
        if not primary_ings:
            continue

        pair_ings = [
            p for p in matched if any(getattr(p, d, 0.0) >= 0.3 for d in pair_dims)
        ]
        if pair_ings:
            names = list(dict.fromkeys([p.name for p in primary_ings + pair_ings]))
            palate_boost = sum(getattr(palate, d, 0.5) for d in [primary] + pair_dims) / (1 + len(pair_dims))
            score = round(rule.weight * (0.7 + palate_boost), 3)
            applied_rules.append(
                AppliedRuleMatch(
                    rule_id=rule.id,
                    title=rule.title,
                    rationale=rule.rationale,
                    matched_ingredients=names,
                    score=score,
                )
            )
        else:
            # Suggest an ingredient from the knowledgebase that supplies the missing counter-taste
            candidates = [
                p.name
                for p in all_profiles
                if any(getattr(p, d, 0.0) >= 0.4 for d in pair_dims)
            ][:3]
            cand_str = f" (e.g. {', '.join(candidates)})" if candidates else ""
            missing_hints.append(
                f"You have {', '.join(p.name for p in primary_ings)} ({primary}), "
                f"add {'/'.join(pair_dims)}{cand_str} — {rule.rationale}"
            )

    matched_keys = {p.name_key for p in matched}
    pair_rows = list(
        db.scalars(
            select(IngredientPairAffinity).order_by(IngredientPairAffinity.affinity_score.desc())
        ).all()
    )
    top_pairs = [
        PairAffinityOut(
            ingredient_a=pr.ingredient_a,
            ingredient_b=pr.ingredient_b,
            affinity_score=round(pr.affinity_score, 3),
            times_paired=pr.times_paired,
        )
        for pr in pair_rows
        if pr.ingredient_a in matched_keys and pr.ingredient_b in matched_keys
    ][:5]

    applied_rules.sort(key=lambda r: r.score, reverse=True)
    return TasteAnalysisOut(
        palate=_to_palate_out(palate),
        applied_rules=applied_rules,
        missing_balance_hints=missing_hints[:4],
        matched_profiles=[_to_profile_out(p) for p in matched],
        top_pair_affinities=top_pairs,
    )


def record_feedback(db: Session, req: TasteFeedbackRequest) -> TasteFeedbackOut:
    """Record meal feedback and update personal palate weights + rule weights."""
    palate = _get_or_create_palate(db)
    all_profiles = list(db.scalars(select(IngredientTasteProfile)).all())
    matched = _match_profiles(all_profiles, req.ingredients_used)

    verdict = req.verdict
    if verdict == "loved":
        for dim in TASTE_DIMS:
            avg_dim = sum(getattr(p, dim, 0.0) for p in matched) / max(1, len(matched))
            if avg_dim >= 0.3:
                setattr(palate, dim, _clamp(getattr(palate, dim) + 0.05))
        for rid in req.applied_rule_ids:
            rule = db.get(TasteRule, rid)
            if rule:
                rule.weight = min(3.0, round(rule.weight + 0.15, 3))
                rule.times_applied += 1
    elif verdict == "balanced":
        for rid in req.applied_rule_ids:
            rule = db.get(TasteRule, rid)
            if rule:
                rule.weight = min(3.0, round(rule.weight + 0.08, 3))
                rule.times_applied += 1
    elif verdict == "too_spicy":
        palate.spicy = _clamp(palate.spicy - 0.10)
        for rule in db.scalars(select(TasteRule).where(TasteRule.primary_taste == "spicy")).all():
            rule.weight = min(3.0, round(rule.weight + 0.12, 3))
    elif verdict == "needs_acid":
        palate.sour = _clamp(palate.sour + 0.08)
        for rule in db.scalars(select(TasteRule).where(TasteRule.pair_tastes.contains("sour"))).all():
            rule.weight = min(3.0, round(rule.weight + 0.10, 3))
    elif verdict == "too_salty":
        palate.salty = _clamp(palate.salty - 0.08)
    elif verdict == "too_sweet":
        palate.sweet = _clamp(palate.sweet - 0.08)
    elif verdict == "too_bland":
        palate.umami = _clamp(palate.umami + 0.06)
        palate.spicy = _clamp(palate.spicy + 0.05)

    palate.feedback_count += 1
    palate.updated_at = datetime.now(timezone.utc)

    # Update pairwise ingredient affinity scores
    pair_delta = 0.25 if verdict == "loved" else (0.12 if verdict == "balanced" else -0.08)
    matched_names = sorted({p.name_key for p in matched})
    for idx_a in range(len(matched_names)):
        for idx_b in range(idx_a + 1, len(matched_names)):
            a_key, b_key = matched_names[idx_a], matched_names[idx_b]
            pair_row = db.scalar(
                select(IngredientPairAffinity).where(
                    IngredientPairAffinity.ingredient_a == a_key,
                    IngredientPairAffinity.ingredient_b == b_key,
                )
            )
            if not pair_row:
                pair_row = IngredientPairAffinity(
                    ingredient_a=a_key,
                    ingredient_b=b_key,
                    affinity_score=0.0,
                    times_paired=0,
                )
                db.add(pair_row)
            pair_row.affinity_score = round(max(-1.0, min(2.0, (pair_row.affinity_score or 0.0) + pair_delta)), 3)
            pair_row.times_paired = (pair_row.times_paired or 0) + 1
            pair_row.updated_at = datetime.now(timezone.utc)

    fb = TasteFeedback(
        dish_title=req.dish_title.strip(),
        ingredients_used=", ".join(req.ingredients_used),
        applied_rule_ids=",".join(str(i) for i in req.applied_rule_ids),
        verdict=verdict,
        notes=req.notes.strip(),
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    db.refresh(palate)
    return TasteFeedbackOut(
        id=fb.id,
        dish_title=fb.dish_title,
        verdict=fb.verdict,
        notes=fb.notes,
        palate=_to_palate_out(palate),
        created_at=fb.created_at,
    )


def list_feedback_logs(db: Session, limit: int = 15) -> list[TasteFeedbackLogOut]:
    rows = list(
        db.scalars(select(TasteFeedback).order_by(TasteFeedback.created_at.desc()).limit(limit)).all()
    )
    return [
        TasteFeedbackLogOut(
            id=r.id,
            dish_title=r.dish_title,
            ingredients_used=r.ingredients_used,
            verdict=r.verdict,
            notes=r.notes,
            created_at=r.created_at,
        )
        for r in rows
    ]


_KEYWORD_ESTIMATES = [
    (("tomato",), dict(sweet=0.4, sour=0.7, umami=0.6, texture="juicy", aroma="fresh", cooking_method="saute", notes="Bright acidity + natural sweetness; tames spicy heat.")),
    (("chili", "chilli", "cili", "jalapeno", "pepper"), dict(spicy=0.9, bitter=0.1, texture="crisp", aroma="pungent", cooking_method="stir-fry", notes="Sharp capsaicin heat; pairs well with sour & sweet.")),
    (("lime", "lemon", "calamansi", "vinegar", "tamarind", "asam"), dict(sour=0.95, sweet=0.1, texture="liquid", aroma="citrus", cooking_method="raw", notes="Bright acid that cuts richness and lifts flavor.")),
    (("sugar", "honey", "gula", "maple"), dict(sweet=0.95, texture="granulated", aroma="caramel", cooking_method="glaze", notes="Rounds out heat, acid, and bitterness.")),
    (("soy", "kicap", "miso", "fish sauce", "oyster", "belacan"), dict(salty=0.85, umami=0.85, texture="liquid", aroma="savory", cooking_method="stir-fry", notes="Deep umami and saltiness.")),
    (("garlic", "ginger", "onion", "shallot"), dict(umami=0.45, spicy=0.25, sweet=0.2, texture="crisp", aroma="pungent", cooking_method="saute", notes="Aromatic foundation for stir-fries and soups.")),
    (("egg", "cheese", "butter", "cream", "coconut"), dict(umami=0.35, richness=0.75, texture="creamy", aroma="rich", cooking_method="sear", notes="Richness coats spice and adds body.")),
    (("chicken", "beef", "fish", "tuna", "prawn", "tofu"), dict(umami=0.5, texture="tender", aroma="mild", cooking_method="sear", notes="Protein base that carries bold sauces.")),
    (("kangkung", "spinach", "kailan", "cabbage", "broccoli", "lettuce"), dict(bitter=0.2, sweet=0.15, umami=0.2, texture="crisp", aroma="earthy", cooking_method="stir-fry", notes="Leafy green; absorbs garlic and umami sauces.")),
]


def _heuristic_estimate(name: str) -> TasteProfileUpsert:
    lower = name.lower()
    for keywords, props in _KEYWORD_ESTIMATES:
        if any(k in lower for k in keywords):
            return TasteProfileUpsert(name=name, **props)
    return TasteProfileUpsert(
        name=name,
        umami=0.3,
        texture="tender",
        aroma="mild",
        cooking_method="stir-fry",
        notes="Auto-estimated baseline; adjust sliders to refine.",
    )


def auto_profile_unknown_ingredients(db: Session, item_names: list[str]) -> AutoProfileOut:
    """Auto-estimate taste profiles for fridge items that have no profile yet (Ollama or heuristic)."""
    existing = list(db.scalars(select(IngredientTasteProfile)).all())
    unknown = [
        name
        for name in dict.fromkeys(n.strip() for n in item_names if n.strip())
        if not _match_profiles(existing, [name])
    ]
    if not unknown:
        return AutoProfileOut(created_profiles=[], engine="none_needed")

    engine_used = "heuristic_fallback"
    created: list[TasteProfileOut] = []

    llm_map: dict[str, dict] = {}
    try:
        with httpx.Client(timeout=0.8) as probe:
            tags_resp = probe.get("http://127.0.0.1:11434/api/tags")
            models = tags_resp.json().get("models", []) if tags_resp.status_code == 200 else []
            model_name = models[0]["name"] if models else None
        if model_name:
            prompt = (
                "Return ONLY a JSON object mapping each ingredient to its flavor profile: "
                "{sweet, sour, salty, bitter, umami, spicy, richness (0.0-1.0), texture, aroma, cooking_method, notes}. "
                f"Ingredients: {', '.join(unknown)}"
            )
            import time as _time
            from app.modules.settings.ai_usage import record_ai_usage

            t0 = _time.monotonic()
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(
                    "http://127.0.0.1:11434/api/generate",
                    json={"model": model_name, "prompt": prompt, "format": "json", "stream": False},
                )
                if resp.status_code == 200 and resp.json().get("response"):
                    rjson = resp.json()
                    record_ai_usage(
                        "ollama",
                        model_name,
                        "kitchen_taste",
                        status="ok",
                        input_tokens=int(rjson.get("prompt_eval_count") or 0),
                        output_tokens=int(rjson.get("eval_count") or 0),
                        latency_ms=int((_time.monotonic() - t0) * 1000),
                    )
                    parsed = json.loads(rjson["response"])
                    if isinstance(parsed, dict):
                        llm_map = {str(k).lower(): v for k, v in parsed.items() if isinstance(v, dict)}
                        engine_used = "ollama_local"
    except Exception:
        pass

    for name in unknown:
        data = llm_map.get(name.lower())
        if data:
            req = TasteProfileUpsert(
                name=name,
                sweet=float(data.get("sweet", 0.0) or 0.0),
                sour=float(data.get("sour", 0.0) or 0.0),
                salty=float(data.get("salty", 0.0) or 0.0),
                bitter=float(data.get("bitter", 0.0) or 0.0),
                umami=float(data.get("umami", 0.0) or 0.0),
                spicy=float(data.get("spicy", 0.0) or 0.0),
                richness=float(data.get("richness", 0.0) or 0.0),
                texture=str(data.get("texture", ""))[:64],
                aroma=str(data.get("aroma", ""))[:64],
                cooking_method=str(data.get("cooking_method", ""))[:64],
                notes=str(data.get("notes", ""))[:500],
            )
        else:
            req = _heuristic_estimate(name)
        created.append(upsert_profile(db, req))

    return AutoProfileOut(created_profiles=created, engine=engine_used)

