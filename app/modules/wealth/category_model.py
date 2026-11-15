from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.wealth.models import CategoryModelState, LedgerEntry

_STOPWORDS = {
    "the", "and", "for", "from", "with", "your", "you", "via", "using", "main", "account",
    "payment", "completed", "transfer", "settled", "successful", "myr", "rm", "sdn", "bhd",
    "touch", "ngo", "statement", "ryt", "bank", "cimb", "public", "duitnow", "wallet",
    "card", "gmt", "this", "was", "not", "has", "been", "into", "out",
    "tngow3my1", "tngd", "qr", "recei", "vefrom", "receive", "notification", "fund",
    "to", "pm", "bin", "binti", "ap", "al", "plt", "enterprise", "resources", "services",
    "trans", "ferto",
}

_EVERYDAY_NO_DATE_CATEGORIES = {
    "Food & Beverage",
    "Daily Interest",
    "Groceries & Mart",
    "Fuel/Toll/parking",
    "Transport",
    "Parking",
    "Shopping & Retail",
    "Badminton",
    "P2P Transfer In",
    "P2P Transfer Out",
}


def _format_feature_label(tok: str) -> str:
    if tok.startswith("m:"):
        return tok[2:]
    if tok.startswith("amt:"):
        return f"RM {tok[4:]}"
    if tok.startswith("amt_ge_"):
        return f">= RM {tok[7:]}"
    if tok.startswith("amt_lt_"):
        return f"< RM {tok[7:]}"
    if tok.startswith("amt_10:"):
        return f"~RM {tok[7:]}"
    if tok.startswith("m_amt:"):
        _, m, tag = tok.split(":", 2)
        return f"{m} ({'>=800' if tag == 'ge800' else '<800'})"
    if tok.startswith("dom_bucket:"):
        b = tok.split(":", 1)[1]
        return {"early": "Day 1-10", "mid": "Day 11-20", "late": "Day 21-31"}.get(b, b)
    if tok == "freq:once_month":
        return "1x/month"
    if tok == "freq:multi_month":
        return "2+/month"
    if tok.startswith("m_freq:"):
        _, m, tag = tok.split(":", 2)
        return f"{m} ({'1x/mo' if tag == 'once' else '2+/mo'})"
    if tok.startswith("acct:"):
        return tok[5:]
    return tok


def _clean_merchant_words(merchant: str) -> list[str]:
    clean = re.sub(r"[^a-z0-9]+", " ", (merchant or "").lower()).strip()
    return [w for w in clean.split() if len(w) >= 2 and not any(ch.isdigit() for ch in w) and w not in _STOPWORDS]


def _merchant_key(merchant: str) -> str:
    return " ".join(_clean_merchant_words(merchant)[:3])


def _canonical_merchant_key(merchant: str) -> str:
    return " ".join(sorted(_merchant_key(merchant).split()))


def _amount_bucket(amount_myr: float) -> str:
    if amount_myr < 10:
        return "<10"
    if amount_myr < 30:
        return "10-30"
    if amount_myr < 100:
        return "30-100"
    if amount_myr < 300:
        return "100-300"
    if amount_myr < 800:
        return "300-800"
    if amount_myr < 1500:
        return "800-1500"
    return "1500+"


def extract_features(
    merchant: str,
    subject: str = "",
    sender_email: str = "",
    account_name: str = "",
    tx_type: str = "",
    amount_myr: float | None = None,
    occurred_at: datetime | None = None,
    monthly_count: int | None = None,
) -> list[str]:
    """Tokenize transaction text, amount bands, day-of-month, and monthly cadence features."""
    tokens: list[str] = []
    words = _clean_merchant_words(merchant)
    # Weight merchant tokens higher by emitting them twice + bigrams
    for w in words:
        tokens.append(f"m:{w}")
        tokens.append(f"m:{w}")
        if len(w) >= 4:
            tokens.append(f"p:{w[:4]}")
    for i in range(len(words) - 1):
        tokens.append(f"bg:{words[i]}_{words[i + 1]}")

    subj_words = [
        w
        for w in re.sub(r"[^a-z0-9]+", " ", (subject or "").lower()).split()
        if len(w) >= 3 and not any(ch.isdigit() for ch in w) and w not in _STOPWORDS
    ]
    for w in subj_words[:10]:
        tokens.append(f"s:{w}")

    if sender_email:
        tokens.append(f"snd:{sender_email.lower().strip()}")
    if account_name:
        tokens.append(f"acct:{account_name.lower().strip()}")
    if tx_type:
        tokens.append(f"tx:{tx_type.lower().strip()}")

    lead_m = _merchant_key(merchant)
    if amount_myr is not None:
        tokens.append(f"amt:{_amount_bucket(amount_myr)}")
        if amount_myr >= 800:
            tokens.append("amt_ge_800")
        if amount_myr >= 50:
            tokens.append(f"amt_10:{int(round(amount_myr / 10.0) * 10)}")
        if lead_m:
            tag = "ge800" if amount_myr >= 800 else "lt800"
            tokens.append(f"m_amt:{lead_m}:{tag}")
            tokens.append(f"m_amt:{lead_m}:{tag}")

    if occurred_at is not None and monthly_count == 1 and (amount_myr is None or amount_myr >= 50):
        d = occurred_at.day
        tokens.append(f"dom:{d}")
        tokens.append("dom_bucket:early" if d <= 10 else ("dom_bucket:mid" if d <= 20 else "dom_bucket:late"))

    if monthly_count is not None and monthly_count > 0:
        freq_tag = "once" if monthly_count == 1 else "multi"
        tokens.append(f"freq:{freq_tag}_month")
        if lead_m:
            tokens.append(f"m_freq:{lead_m}:{freq_tag}")

    return tokens


def get_model_state(db: Session) -> CategoryModelState | None:
    return db.scalar(select(CategoryModelState))


def _monthly_merchant_counts(rows: list[LedgerEntry]) -> dict[tuple[str, str], int]:
    counts: Counter[tuple[str, str]] = Counter()
    for r in rows:
        mk = _merchant_key(r.merchant)
        if mk and r.occurred_at:
            counts[(mk, r.occurred_at.strftime("%Y-%m"))] += 1
    return counts


def train_category_model(db: Session) -> dict[str, Any]:
    """Train a Laplace-smoothed Multinomial Naive Bayes classifier from ledger history."""
    rows = list(db.scalars(select(LedgerEntry)).all())
    monthly_counts = _monthly_merchant_counts(rows)

    class_doc_weights: dict[str, float] = defaultdict(float)
    token_counts: dict[str, Counter[str]] = defaultdict(Counter)
    class_total_tokens: dict[str, float] = defaultdict(float)
    vocab: set[str] = set()

    eligible_rows: list[tuple[LedgerEntry, float, list[str], str]] = []
    entity_cats: dict[str, set[str]] = defaultdict(set)

    for r in rows:
        if r.is_self_transfer:
            continue
        # Learn strongly from explicit user overrides, and lightly from existing non-General tags
        if r.manual_override:
            weight = 3.0
        elif r.category and r.category not in ("General", "Bank / Retail", "Parking & E-Wallet"):
            weight = 1.0
        else:
            continue

        mc = monthly_counts.get((_merchant_key(r.merchant), r.occurred_at.strftime("%Y-%m")), 1) if r.occurred_at else None
        feats = extract_features(
            r.merchant,
            r.subject,
            r.sender_email,
            r.account_name,
            r.tx_type,
            amount_myr=r.amount_myr,
            occurred_at=r.occurred_at,
            monthly_count=mc,
        )
        if not feats:
            continue

        ce = _canonical_merchant_key(r.merchant)
        if ce and r.category:
            entity_cats[ce].add(r.category)
        eligible_rows.append((r, weight, feats, ce))

    multi_cat_entities = {ce for ce, cats in entity_cats.items() if len(cats) > 1}

    sample_count = 0
    manual_count = 0
    for r, weight, feats, ce in eligible_rows:
        if ce in multi_cat_entities:
            feats = [t for t in feats if not t.startswith(("m:", "bg:", "p:"))]
        if not feats:
            continue

        sample_count += 1
        if r.manual_override:
            manual_count += 1
        cat = r.category
        class_doc_weights[cat] += weight
        for tok in feats:
            token_counts[cat][tok] += weight
            class_total_tokens[cat] += weight

    # Remove merchant-identity / entity-pair tokens shared across multiple categories
    shared_tok_cats: dict[str, set[str]] = defaultdict(set)
    for cat, cnts in token_counts.items():
        for tok, cnt in cnts.items():
            if cnt > 0 and tok.startswith(("m:", "bg:", "p:", "m_amt:", "m_freq:")):
                shared_tok_cats[tok].add(cat)
    dup_tokens = {tok for tok, cats in shared_tok_cats.items() if len(cats) > 1}
    if dup_tokens:
        for cat, cnts in token_counts.items():
            for tok in list(cnts.keys()):
                if tok in dup_tokens:
                    removed = cnts.pop(tok, 0.0)
                    class_total_tokens[cat] = max(0.0, class_total_tokens[cat] - removed)

    vocab = {tok for cnts in token_counts.values() for tok, cnt in cnts.items() if cnt > 0}

    # Extract human-readable top merchant tokens & pattern signals per category
    top_keywords: dict[str, list[str]] = {}
    top_patterns: dict[str, dict[str, Any]] = {}
    for cat, counts in token_counts.items():
        min_badge_cnt = 0.5 * class_doc_weights[cat]
        hide_dates = (
            cat in _EVERYDAY_NO_DATE_CATEGORIES
            or counts.get("freq:multi_month", 0.0) > counts.get("freq:once_month", 0.0)
        )
        merch_tokens = sorted(
            ((tok[2:], cnt) for tok, cnt in counts.items() if tok.startswith("m:") and cnt > 0),
            key=lambda x: x[1],
            reverse=True,
        )
        amt_tokens = sorted(
            (
                (_format_feature_label(tok), cnt)
                for tok, cnt in counts.items()
                if tok.startswith(("amt:", "amt_ge_")) and cnt >= min_badge_cnt
            ),
            key=lambda x: x[1],
            reverse=True,
        )
        time_tokens = sorted(
            (
                (_format_feature_label(tok), cnt)
                for tok, cnt in counts.items()
                if (
                    tok.startswith("freq:")
                    or (tok.startswith("dom_bucket:") and not hide_dates)
                )
                and cnt >= min_badge_cnt
            ),
            key=lambda x: x[1],
            reverse=True,
        )
        kws = [t for t, _ in merch_tokens[:6]]
        if not kws:
            m_amt_tokens = sorted(
                ((_format_feature_label(tok), cnt) for tok, cnt in counts.items() if tok.startswith("m_amt:") and cnt > 0),
                key=lambda x: x[1],
                reverse=True,
            )
            kws = [lbl for lbl, _ in m_amt_tokens[:3]]
        top_keywords[cat] = kws
        top_patterns[cat] = {
            "keywords": kws,
            "amounts": [t for t, _ in amt_tokens[:3]],
            "timing": [t for t, _ in time_tokens[:3]],
            "weight": round(class_doc_weights[cat], 1),
        }

    payload = {
        "class_doc_weights": dict(class_doc_weights),
        "token_counts": {cat: dict(cnt) for cat, cnt in token_counts.items()},
        "class_total_tokens": dict(class_total_tokens),
        "vocab_size": len(vocab),
        "top_keywords": top_keywords,
        "top_patterns": top_patterns,
    }

    now = datetime.now(timezone.utc)
    state = db.scalar(select(CategoryModelState))
    if not state:
        state = CategoryModelState()
        db.add(state)
    state.sample_count = sample_count
    state.manual_sample_count = manual_count
    state.categories_count = len(class_doc_weights)
    state.model_json = payload
    state.trained_at = now
    db.commit()
    db.refresh(state)
    return summarize_model_state(state)


def predict_category(
    model_json: dict[str, Any] | None,
    merchant: str,
    subject: str = "",
    sender_email: str = "",
    account_name: str = "",
    tx_type: str = "",
    min_confidence: float = 0.55,
    amount_myr: float | None = None,
    occurred_at: datetime | None = None,
    monthly_count: int | None = None,
) -> tuple[str | None, float]:
    """Return (predicted_category, confidence) using Naive Bayes posterior probabilities."""
    if not model_json:
        return None, 0.0
    class_weights: dict[str, float] = model_json.get("class_doc_weights") or {}
    if len(class_weights) < 1:
        return None, 0.0

    token_counts: dict[str, dict[str, float]] = model_json.get("token_counts") or {}
    class_totals: dict[str, float] = model_json.get("class_total_tokens") or {}
    vocab_size = max(1, int(model_json.get("vocab_size") or 1))
    total_docs = sum(class_weights.values()) or 1.0

    feats = extract_features(
        merchant,
        subject,
        sender_email,
        account_name,
        tx_type,
        amount_myr=amount_myr,
        occurred_at=occurred_at,
        monthly_count=monthly_count,
    )
    if not feats:
        return None, 0.0

    log_probs: dict[str, float] = {}
    alpha = 0.01
    norm_denom = 1.0 + alpha * vocab_size
    content_feats = [t for t in feats if not t.startswith("acct:") and not t.startswith("tx:")]
    matched_any_content = False
    for cat, doc_w in class_weights.items():
        lp = math.log(doc_w / total_docs)
        cat_total = max(1.0, float(class_totals.get(cat, 0.0)))
        cat_counts = token_counts.get(cat) or {}
        if any(tok in cat_counts for tok in content_feats):
            matched_any_content = True
        for tok in feats:
            freq = float(cat_counts.get(tok, 0.0)) / cat_total
            lp += math.log((freq + alpha) / norm_denom)
        log_probs[cat] = lp

    if content_feats and not matched_any_content:
        return None, 0.0

    if len(log_probs) == 1:
        only_cat = next(iter(log_probs))
        return only_cat, 1.0

    max_lp = max(log_probs.values())
    exp_probs = {cat: math.exp(lp - max_lp) for cat, lp in log_probs.items()}
    norm = sum(exp_probs.values()) or 1.0
    best_cat, best_exp = max(exp_probs.items(), key=lambda x: x[1])
    confidence = round(best_exp / norm, 4)
    if confidence < min_confidence:
        return None, confidence
    return best_cat, confidence


def preview_suggestions(db: Session, limit: int = 30, min_confidence: float = 0.55) -> list[dict[str, Any]]:
    """Preview category predictions on unedited entries that differ from their current category."""
    state = get_model_state(db)
    if not state or not state.model_json:
        return []

    all_rows = list(db.scalars(select(LedgerEntry)).all())
    monthly_counts = _monthly_merchant_counts(all_rows)
    rows = [r for r in all_rows if not r.manual_override and not r.is_self_transfer]
    rows.sort(key=lambda r: r.occurred_at, reverse=True)
    suggestions: list[dict[str, Any]] = []
    for r in rows:
        mc = monthly_counts.get((_merchant_key(r.merchant), r.occurred_at.strftime("%Y-%m")), 1) if r.occurred_at else None
        pred, conf = predict_category(
            state.model_json,
            r.merchant,
            r.subject,
            r.sender_email,
            r.account_name,
            r.tx_type,
            min_confidence=min_confidence,
            amount_myr=r.amount_myr,
            occurred_at=r.occurred_at,
            monthly_count=mc,
        )
        if pred and pred != r.category:
            feats = extract_features(
                r.merchant, r.subject, r.sender_email, r.account_name, r.tx_type,
                amount_myr=r.amount_myr, occurred_at=r.occurred_at, monthly_count=mc,
            )
            cat_counts = (state.model_json.get("token_counts") or {}).get(pred) or {}
            ranked_reasons = sorted(
                {
                    _format_feature_label(t): float(cat_counts.get(t, 0.0))
                    for t in feats
                    if cat_counts.get(t, 0.0) > 0
                    and t.startswith(("m:", "m_amt:", "amt_ge_", "amt_lt_", "amt:", "dom_bucket:", "freq:", "m_freq:", "acct:"))
                }.items(),
                key=lambda x: x[1],
                reverse=True,
            )
            suggestions.append(
                {
                    "entry_id": r.id,
                    "merchant": r.merchant,
                    "occurred_at": r.occurred_at.isoformat(),
                    "account_name": r.account_name,
                    "amount_myr": r.amount_myr,
                    "tx_type": r.tx_type,
                    "monthly_count": mc,
                    "current_category": r.category,
                    "suggested_category": pred,
                    "confidence": conf,
                    "reasons": [lbl for lbl, _ in ranked_reasons[:5]],
                }
            )
            if len(suggestions) >= limit:
                break
    return suggestions


def apply_suggestions(db: Session, entry_ids: list[int] | None = None, min_confidence: float = 0.55) -> int:
    """Apply the trained model's predictions to unedited entries."""
    state = get_model_state(db)
    if not state or not state.model_json:
        return 0

    stmt = select(LedgerEntry).where(LedgerEntry.manual_override.is_(False), LedgerEntry.is_self_transfer.is_(False))
    if entry_ids is not None:
        if not entry_ids:
            return 0
        stmt = stmt.where(LedgerEntry.id.in_(entry_ids))

    all_rows = list(db.scalars(select(LedgerEntry)).all())
    monthly_counts = _monthly_merchant_counts(all_rows)
    rows = list(db.scalars(stmt).all())
    updated = 0
    for r in rows:
        mc = monthly_counts.get((_merchant_key(r.merchant), r.occurred_at.strftime("%Y-%m")), 1) if r.occurred_at else None
        pred, _conf = predict_category(
            state.model_json,
            r.merchant,
            r.subject,
            r.sender_email,
            r.account_name,
            r.tx_type,
            min_confidence=min_confidence,
            amount_myr=r.amount_myr,
            occurred_at=r.occurred_at,
            monthly_count=mc,
        )
        if pred and pred != r.category:
            r.category = pred
            if pred.lower() == "business claim":
                r.is_claimable = True
            updated += 1
    db.commit()
    return updated


def summarize_model_state(state: CategoryModelState | None) -> dict[str, Any]:
    if not state:
        return {
            "trained": False,
            "sample_count": 0,
            "manual_sample_count": 0,
            "categories_count": 0,
            "vocab_size": 0,
            "trained_at": None,
            "top_keywords": {},
            "top_patterns": {},
        }
    mj = state.model_json or {}
    trained_at_iso = (
        (state.trained_at if state.trained_at.tzinfo else state.trained_at.replace(tzinfo=timezone.utc)).isoformat()
        if state.trained_at
        else None
    )
    return {
        "trained": state.sample_count > 0,
        "sample_count": state.sample_count,
        "manual_sample_count": state.manual_sample_count,
        "categories_count": state.categories_count,
        "vocab_size": int(mj.get("vocab_size") or 0),
        "trained_at": trained_at_iso,
        "top_keywords": mj.get("top_keywords", {}),
        "top_patterns": mj.get("top_patterns", {}),
    }
