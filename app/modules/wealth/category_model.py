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
}


def extract_features(
    merchant: str,
    subject: str = "",
    sender_email: str = "",
    account_name: str = "",
    tx_type: str = "",
) -> list[str]:
    """Tokenize transaction text into normalized word and prefix features."""
    tokens: list[str] = []
    clean_merch = re.sub(r"[^a-z0-9]+", " ", (merchant or "").lower()).strip()
    words = [w for w in clean_merch.split() if len(w) >= 2 and not w.isdigit() and w not in _STOPWORDS]
    # Weight merchant tokens higher by emitting them twice + bigrams
    for w in words:
        tokens.append(f"m:{w}")
        tokens.append(w)
        if len(w) >= 4:
            tokens.append(f"p:{w[:4]}")
    for i in range(len(words) - 1):
        tokens.append(f"bg:{words[i]}_{words[i + 1]}")

    subj_words = [
        w
        for w in re.sub(r"[^a-z0-9]+", " ", (subject or "").lower()).split()
        if len(w) >= 3 and not w.isdigit() and w not in _STOPWORDS
    ]
    for w in subj_words[:10]:
        tokens.append(f"s:{w}")

    if sender_email:
        tokens.append(f"snd:{sender_email.lower().strip()}")
    if account_name:
        tokens.append(f"acct:{account_name.lower().strip()}")
    if tx_type:
        tokens.append(f"tx:{tx_type.lower().strip()}")
    return tokens


def get_model_state(db: Session) -> CategoryModelState | None:
    return db.get(CategoryModelState, 1)


def train_category_model(db: Session) -> dict[str, Any]:
    """Train a Laplace-smoothed Multinomial Naive Bayes classifier from ledger history."""
    rows = list(db.scalars(select(LedgerEntry)).all())

    class_doc_weights: dict[str, float] = defaultdict(float)
    token_counts: dict[str, Counter[str]] = defaultdict(Counter)
    class_total_tokens: dict[str, float] = defaultdict(float)
    vocab: set[str] = set()

    sample_count = 0
    manual_count = 0

    for r in rows:
        if r.is_self_transfer:
            continue
        # Learn strongly from explicit user overrides, and lightly from existing non-General tags
        if r.manual_override:
            weight = 3.0
            manual_count += 1
        elif r.category and r.category not in ("General", "Bank / Retail"):
            weight = 1.0
        else:
            continue

        feats = extract_features(r.merchant, r.subject, r.sender_email, r.account_name, r.tx_type)
        if not feats:
            continue

        sample_count += 1
        cat = r.category
        class_doc_weights[cat] += weight
        for tok in feats:
            token_counts[cat][tok] += weight
            class_total_tokens[cat] += weight
            vocab.add(tok)

    # Extract human-readable top merchant tokens per category
    top_keywords: dict[str, list[str]] = {}
    for cat, counts in token_counts.items():
        merch_tokens = [
            (tok[2:], cnt)
            for tok, cnt in counts.items()
            if tok.startswith("m:")
        ]
        merch_tokens.sort(key=lambda x: x[1], reverse=True)
        top_keywords[cat] = [t for t, _ in merch_tokens[:6]]

    payload = {
        "class_doc_weights": dict(class_doc_weights),
        "token_counts": {cat: dict(cnt) for cat, cnt in token_counts.items()},
        "class_total_tokens": dict(class_total_tokens),
        "vocab_size": len(vocab),
        "top_keywords": top_keywords,
    }

    now = datetime.now(timezone.utc)
    state = db.get(CategoryModelState, 1)
    if not state:
        state = CategoryModelState(id=1)
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

    feats = extract_features(merchant, subject, sender_email, account_name, tx_type)
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

    rows = list(
        db.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.manual_override.is_(False), LedgerEntry.is_self_transfer.is_(False))
            .order_by(LedgerEntry.occurred_at.desc())
        ).all()
    )
    suggestions: list[dict[str, Any]] = []
    for r in rows:
        pred, conf = predict_category(
            state.model_json,
            r.merchant,
            r.subject,
            r.sender_email,
            r.account_name,
            r.tx_type,
            min_confidence=min_confidence,
        )
        if pred and pred != r.category:
            suggestions.append(
                {
                    "entry_id": r.id,
                    "merchant": r.merchant,
                    "occurred_at": r.occurred_at.isoformat(),
                    "account_name": r.account_name,
                    "amount_myr": r.amount_myr,
                    "current_category": r.category,
                    "suggested_category": pred,
                    "confidence": conf,
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

    rows = list(db.scalars(stmt).all())
    updated = 0
    for r in rows:
        pred, _conf = predict_category(
            state.model_json,
            r.merchant,
            r.subject,
            r.sender_email,
            r.account_name,
            r.tx_type,
            min_confidence=min_confidence,
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
            "trained_at": None,
            "top_keywords": {},
        }
    return {
        "trained": state.sample_count > 0,
        "sample_count": state.sample_count,
        "manual_sample_count": state.manual_sample_count,
        "categories_count": state.categories_count,
        "trained_at": state.trained_at.isoformat() if state.trained_at else None,
        "top_keywords": (state.model_json or {}).get("top_keywords", {}),
    }
