from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.modules.wealth.email_client import RawEmail

_MONEY_KEYWORDS = {
    "receipt": 3,
    "payment": 3,
    "invoice": 3,
    "transfer": 3,
    "duitnow": 4,
    "ewallet": 4,
    "bank": 3,
    "statement": 3,
    "charged": 3,
    "paid": 2,
    "ride": 2,
    "parking": 3,
    "fuel": 3,
    "payout": 4,
    "deposited": 4,
    "maybank": 5,
    "cimb": 5,
    "rhb": 5,
    "hongleong": 5,
    "publicbank": 5,
    "rytbank": 5,
    "ryt bank": 5,
    "touchngo": 5,
    "tng": 4,
    "bolt": 4,
    "grab": 4,
    "setel": 4,
    "wise": 5,
    "stripe": 4,
    "paypal": 4,
    "rm ": 2,
    "myr": 3,
}

_JUNK_KEYWORDS = {
    "unsubscribe": -2,
    "sale": -3,
    "mega sale": -4,
    "voucher": -3,
    "cashback": -2,
    "newsletter": -4,
    "promo": -4,
    "marketing": -6,
    "greetings@": -5,
    "jobs": -4,
    "hiring": -3,
    "digest": -3,
    "pidm": -5,
    "lucky draw": -6,
    "win up to": -6,
}

_AMOUNT_RE = re.compile(r"(?:RM|MYR)\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)", re.IGNORECASE)

_TX_AMOUNT_PATTERNS = [
    re.compile(
        r"(?:total\s+(?:paid|charged|amount)|amount(?:\s+paid)?|transaction\s+amount)\s*[:\-]?\s*(?:RM|MYR)\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:paid|spent|transferred|charged|received|credited|deposited|deducted|withdrawn)\s+(?:of\s+)?(?:RM|MYR)\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)",
        re.IGNORECASE,
    ),
]

_NON_TX_SIGNALS = (
    "protected by pidm",
    "pidm",
    "win up to",
    "stand a chance to win",
    "lucky draw",
    "terms & conditions apply",
    "terms and conditions apply",
    "scheduled maintenance",
    "unsubscribe from this list",
    "promotional",
    "hit a bump",
    "didn't go through",
    "did not go through",
    "insufficient funds",
    "upcoming scheduled transfer",
)

_TX_ACTION_SIGNALS = (
    "receipt",
    "transaction",
    "transferred",
    "paid",
    "charged",
    "deducted",
    "received",
    "credited",
    "deposited",
    "withdrawn",
    "payment successful",
    "duitnow",
)

_INCOME_SIGNALS = (
    "money in",
    "you've earned",
    "you received",
    "you've received",
    "have received",
    "money received",
    "transfer received",
    "duitnow received",
    "deposited",
    "payout received",
    "retainer received",
    "credited to your",
    "interest credited",
    "interest earned",
    "cashback credited",
    "refund",
    "sent you",
    "incoming transfer",
)

_TNG_MONEY_IN_RE = re.compile(
    r"money\s+in\s*[:\-]?\s*(?:RM|MYR)\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)",
    re.IGNORECASE,
)
_TNG_MONEY_OUT_RE = re.compile(
    r"money\s+out\s*[:\-]?\s*(?:RM|MYR)\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)",
    re.IGNORECASE,
)

_EXPENSE_SIGNALS = (
    "you paid",
    "you have paid",
    "you transferred",
    "payment successful",
    "total charged",
    "total paid",
    "order receipt",
    "order total",
    "you updated your subscription",
    "spent rm",
    "spent myr",
    "deducted",
    "withdrawn",
    "debit",
)


@dataclass
class ParsedTransaction:
    idempotency_hash: str
    sender_email: str
    merchant: str
    category: str
    tx_type: str
    amount_myr: float
    subject: str
    raw_payload: dict


def infer_category_hint(sender_email: str, sender_name: str, text: str) -> str:
    combined = f"{sender_email} {sender_name} {text}".lower()
    if "bolt" in combined or "grab" in combined:
        return "Transport"
    if "parking" in combined or "touchngo" in combined or "tng" in combined:
        return "Parking & E-Wallet"
    if "setel" in combined or "fuel" in combined or "petronas" in combined:
        return "Fuel & Toll"
    if "maybank" in combined or "cimb" in combined or "ryt" in combined or "duitnow" in combined or "bank" in combined:
        return "Bank / Retail"
    return "General"


def match_category_rules(text: str, rules: list[tuple[str, str]]) -> str | None:
    """Return the first matching user-configured category name for a transaction text."""
    lowered = text.lower()
    for cat_name, keywords_csv in rules:
        for raw_kw in (keywords_csv or "").split(","):
            kw = raw_kw.strip().lower()
            if kw and kw in lowered:
                return cat_name
    return None


def match_category_rule_with_flow(
    text: str,
    rules: list,
    occurred_at: datetime | None = None,
    amount_myr: float | None = None,
    tx_type: str | None = None,
    account_name: str | None = None,
) -> tuple[str, str] | None:
    """Return (category_name, flow_type) for the highest-specificity matching rule."""
    lowered = text.lower()
    acct_lower = (account_name or "").lower()
    tx_day = occurred_at.day if occurred_at else None

    def _rule_fields(r):
        if isinstance(r, tuple):
            return (r[0], r[1] or "", r[2] or "auto", None, None, None, None, "any", "")
        return (
            r.name,
            r.keywords or "",
            r.flow_type or "auto",
            r.day_min,
            r.day_max,
            r.amount_min,
            r.amount_max,
            r.tx_direction or "any",
            (r.account_filter or "").strip(),
        )

    _GENERIC_FALLBACK_CATEGORIES = {
        "P2P Transfer In",
        "P2P Transfer Out",
        "Bank / Retail",
        "Parking & E-Wallet",
        "General",
    }

    def _specificity(r) -> tuple[int, int]:
        cat_name, kw, _, dmin, dmax, amin, amax, tdir, acct = _rule_fields(r)
        not_generic = 0 if cat_name in _GENERIC_FALLBACK_CATEGORIES else 1
        score = (
            int(bool(kw.strip()))
            + int(dmin is not None or dmax is not None)
            + int(amin is not None or amax is not None)
            + int(tdir != "any")
            + int(bool(acct))
        )
        return (not_generic, score)

    for rule in sorted(rules, key=_specificity, reverse=True):
        cat_name, keywords_csv, flow_type, dmin, dmax, amin, amax, tdir, acct_filter = _rule_fields(rule)
        kws = [k.strip().lower() for k in keywords_csv.split(",") if k.strip()]
        has_pattern = any([kws, dmin is not None, dmax is not None, amin is not None, amax is not None, acct_filter])
        if not has_pattern:
            continue
        if kws and not any(kw in lowered for kw in kws):
            continue
        if tdir == "expense" and tx_type and tx_type != "expense":
            continue
        if tdir == "income" and tx_type and tx_type not in ("income", "repayment"):
            continue
        if acct_filter and acct_filter.lower() not in acct_lower:
            continue
        if dmin is not None or dmax is not None:
            if tx_day is None:
                continue
            lo = dmin if dmin is not None else 1
            hi = dmax if dmax is not None else 31
            if lo <= hi:
                if not (lo <= tx_day <= hi):
                    continue
            elif not (tx_day >= lo or tx_day <= hi):
                continue
        if amin is not None or amax is not None:
            if amount_myr is None:
                continue
            if amin is not None and amount_myr < amin - 1e-6:
                continue
            if amax is not None and amount_myr > amax + 1e-6:
                continue
        return cat_name, flow_type
    return None


def score_money_sender(sender_email: str, sender_name: str, subjects: list[str]) -> tuple[bool, int, str]:
    """Score a sender from 0+ based on domain/subject signals to separate banks & receipts from junk."""
    combined = f"{sender_email} {sender_name} {' '.join(subjects)}".lower()
    score = 0
    for kw, weight in _MONEY_KEYWORDS.items():
        if kw in combined:
            score += weight
    for kw, weight in _JUNK_KEYWORDS.items():
        if kw in combined:
            score += weight

    category_hint = infer_category_hint(sender_email, sender_name, combined)
    return (score >= 3, score, category_hint)


def build_idempotency_hash(sender_email: str, message_id: str, amount_myr: float, occurred_iso: str) -> str:
    payload = f"{sender_email.lower()}|{message_id}|{amount_myr:.2f}|{occurred_iso}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_promotional_or_non_tx(sender_email: str, subject: str, body: str) -> bool:
    sender_lower = sender_email.lower()
    if any(prefix in sender_lower for prefix in ("marketing@", "hello@marketing.", "promo@", "newsletter@", "greetings@")):
        return True

    subj_lower = subject.lower()
    body_lower = body.lower()

    # Grab sends both "Your Grab E-Receipt" and random promos/guides from the same sender
    if "grab" in sender_lower:
        is_grab_receipt = (
            "e-receipt" in subj_lower
            or "grab receipt" in subj_lower
            or ("hope you enjoyed your" in body_lower and "total" in body_lower)
        )
        if not is_grab_receipt:
            return True

    combined = f"{subj_lower}\n{body_lower}"
    if any(
        promo in combined
        for promo in ("race weekend guide", "has more to offer", "enjoy up to", "read this first", "% off total bill")
    ) and "e-receipt" not in subj_lower:
        return True

    has_non_tx = any(sig in combined for sig in _NON_TX_SIGNALS)
    has_tx_action = any(sig in combined for sig in _TX_ACTION_SIGNALS)
    # Ignore PIDM / promo emails that lack a genuine transaction verb
    if has_non_tx and not has_tx_action:
        return True
    # Even with generic words, failed/bounced/reminder emails are never completed transactions
    if any(
        fail_sig in combined
        for fail_sig in (
            "hit a bump",
            "didn't go through",
            "did not go through",
            "insufficient funds",
            "upcoming scheduled transfer",
            "reminder: upcoming",
        )
    ):
        return True
    return False


def _extract_tx_amount(full_text: str) -> float | None:
    cleaned = re.sub(r"protected\s+by\s+pidm[^.\n]*", " ", full_text, flags=re.IGNORECASE)
    for pattern in _TX_AMOUNT_PATTERNS:
        m = pattern.search(cleaned)
        if m:
            try:
                val = round(float(m.group(1).replace(",", "")), 2)
                if val > 0:
                    return val
            except ValueError:
                continue

    # Fallback: scan all RM/MYR matches, skipping common PIDM 250,000 coverage mentions
    for m in _AMOUNT_RE.finditer(cleaned):
        raw_num = m.group(1).replace(",", "")
        try:
            val = round(float(raw_num), 2)
        except ValueError:
            continue
        if val <= 0:
            continue
        if val == 250000.0 and "pidm" in full_text.lower():
            continue
        return val
    return None


def parse_transaction_emails(raw: RawEmail) -> list[ParsedTransaction]:
    """Extract one or more transactions (e.g. TNG Money In + Money Out summary) from an email."""
    if _is_promotional_or_non_tx(raw.sender_email, raw.subject, raw.body):
        return []

    full_text = f"{raw.subject}\n{raw.body}"
    m_in = _TNG_MONEY_IN_RE.search(full_text)
    m_out = _TNG_MONEY_OUT_RE.search(full_text)
    if m_in or m_out:
        txs: list[ParsedTransaction] = []
        for label, tx_type, match in (
            ("TNG eWallet (Money In)", "income", m_in),
            ("TNG eWallet (Money Out)", "expense", m_out),
        ):
            if not match:
                continue
            val = round(float(match.group(1).replace(",", "")), 2)
            if val <= 0:
                continue
            idem = build_idempotency_hash(
                raw.sender_email, f"{raw.message_id}|{tx_type}", val, raw.received_at.isoformat()
            )
            txs.append(
                ParsedTransaction(
                    idempotency_hash=idem,
                    sender_email=raw.sender_email,
                    merchant=label,
                    category="Parking & E-Wallet",
                    tx_type=tx_type,
                    amount_myr=val,
                    subject=raw.subject,
                    raw_payload={
                        "message_id": raw.message_id,
                        "sender_name": raw.sender_name,
                        "snippet": raw.body[:300],
                        **({"gmail_thread_id": raw.gmail_thread_id} if raw.gmail_thread_id else {}),
                    },
                )
            )
        if txs:
            return txs

    single = parse_transaction_email(raw)
    return [single] if single else []


def extract_merchant_from_text(subject: str, body: str, default_name: str, is_income: bool) -> str:
    """Extract the specific merchant or counterparty name from an email subject/body."""
    lower_text = f"{subject}\n{body}".lower()
    if "jaya grocer" in lower_text:
        return "Jaya Grocer"
    if "nsk" in lower_text:
        return "NSK Trade City"
    if "sunway pyramid" in lower_text:
        return "Sunway Pyramid Parking"
    if "mid valley" in lower_text:
        return "Mid Valley Parking"
    if "pavilion" in lower_text:
        return "Pavilion KL Parking"
    if "trx" in lower_text:
        return "TRX Exchange Parking"
    if "bolt" in lower_text:
        return "Bolt Ride"
    if "setel" in lower_text:
        return "Setel (Petronas)"

    if is_income:
        if "from " in subject.lower():
            return subject.split("from ", 1)[-1].strip()[:80]
        m_refund = re.search(
            r"\bfrom\s+([A-Za-z0-9 @*&'.,\-_/]+?)\s+has\s+been\s+refunded\b",
            body,
            re.IGNORECASE,
        )
        if m_refund:
            return m_refund.group(1).strip()[:80]
        m_from = re.search(
            r"(?:received|transferred|credited|refunded)(?:(?!\.\s)[^\n])*?\bfrom\s+([A-Za-z0-9 @*&'.,\-_/]+?)(?:\s+on\s+\d|\s+at\s+\d|[.\n]|$)",
            body,
            re.IGNORECASE,
        )
        if m_from:
            cand = m_from.group(1).strip()[:80]
            if not any(junk in cand.lower() for junk in ("app store", "click here", "cimb octo")):
                return cand
    else:
        m_paid_at = re.search(
            r"\b(?:paid|spent|charged|purchase[d]?)\s+(?:[^\n.]*?\s+)??at\s+([A-Za-z0-9 @*+&'.,\-_/#():]+?)(?=\s+using\s+your|\s+via\s+|\s+on\s+\d|\s+at\s+\d|[.\n]|$)",
            body,
            re.IGNORECASE,
        )
        if m_paid_at:
            return m_paid_at.group(1).strip()[:80]

        m_to = re.search(
            r"\b(?:transferred|transfer|sent)\s+(?:of\s+)?(?:RM|MYR)\s*[0-9,]+\.[0-9]{2}\s+to\s+([A-Za-z0-9 *&'.,\-_\"]+?(?:\s+\"[^\"]+\")?)(?:\s+using\s+your|\s+from\s+your|\s+on\s+\d|\s+at\s+\d|[.\n]|$)",
            body,
            re.IGNORECASE,
        )
        if m_to:
            return m_to.group(1).strip()[:80]

    return default_name


def parse_transaction_email(raw: RawEmail) -> ParsedTransaction | None:
    """Extract merchant, category, income/expense type, and MYR amount from an email."""
    if _is_promotional_or_non_tx(raw.sender_email, raw.subject, raw.body):
        return None

    full_text = f"{raw.subject}\n{raw.body}"
    amount_myr = _extract_tx_amount(full_text)
    if amount_myr is None:
        return None

    lower_text = full_text.lower()
    # Avoid false positive "refund" match from footer boilerplate like "refund policy"
    cleaned_for_income = lower_text.replace("refund policy", "").replace("no refund", "")
    income_hits = sum(1 for kw in _INCOME_SIGNALS if kw in cleaned_for_income)
    expense_hits = sum(1 for kw in _EXPENSE_SIGNALS if kw in lower_text)
    is_income = income_hits > 0 and income_hits >= expense_hits
    if is_income:
        is_allowance_or_yield = any(
            kw in cleaned_for_income
            for kw in ("allowance", "salary", "gaji", "stipend", "interest", "dividend", "cashback", "refund", "go+ daily earnings")
        )
        is_person_transfer = any(
            kw in cleaned_for_income
            for kw in ("received", "sent you", "money's in", "incoming transfer", "duitnow", "from ")
        )
        tx_type = "repayment" if (is_person_transfer and not is_allowance_or_yield) else "income"
    else:
        tx_type = "expense"
    category = infer_category_hint(raw.sender_email, raw.sender_name, full_text)
    merchant = extract_merchant_from_text(raw.subject, raw.body, raw.sender_name, is_income)

    idem = build_idempotency_hash(raw.sender_email, raw.message_id, amount_myr, raw.received_at.isoformat())
    return ParsedTransaction(
        idempotency_hash=idem,
        sender_email=raw.sender_email,
        merchant=merchant,
        category=category,
        tx_type=tx_type,
        amount_myr=amount_myr,
        subject=raw.subject,
        raw_payload={
            "message_id": raw.message_id,
            "sender_name": raw.sender_name,
            "snippet": raw.body[:300],
            **({"gmail_thread_id": raw.gmail_thread_id} if raw.gmail_thread_id else {}),
        },
    )


def infer_account_name(sender_email: str, subject: str = "", merchant: str = "") -> str:
    """Map a transaction's source/subject/merchant to its individual bank or e-wallet bucket."""
    combined = f"{sender_email} {subject} {merchant}".lower()
    if "touch'ngo" in combined or "touchngo" in combined or "tng" in combined or "go+" in combined:
        return "Touch 'n Go"
    if "ryt" in combined:
        return "Ryt Bank"
    if "cimb" in combined:
        return "CIMB"
    if "publicbank" in combined or "pbe@" in combined or "pbb" in combined or "public bank" in combined:
        return "Public Bank"
    return "Unassigned"


def is_self_transfer_match(merchant: str, subject: str, account_holder_names: str) -> bool:
    """Return True if a transaction transfers to/from one of the user's own account names (any word order)."""
    combined = " ".join(f"{merchant} {subject}".lower().split())
    if "via ewallet to go+" in combined or "via go+" in combined:
        return True
    if not account_holder_names:
        return False

    text_tokens = set(re.findall(r"[a-z0-9]+", combined))
    glued_text = "".join(re.findall(r"[a-z0-9]+", combined))

    for raw_name in account_holder_names.split(","):
        name_tokens = re.findall(r"[a-z0-9]+", raw_name.lower())
        if not name_tokens or sum(len(t) for t in name_tokens) < 3:
            continue
        if all(tok in text_tokens for tok in name_tokens):
            return True
        if "".join(name_tokens) in glued_text:
            return True
    return False


def normalize_merchant_display(merchant: str) -> str:
    """Return a clean human-readable merchant name stripped of noisy DuitNow/TNG/Grab codes."""
    raw = " ".join((merchant or "").split())
    if not raw:
        return ""
    cleaned = raw
    # GO+ Daily EarningsMMF2026110 0 Daily Interest -> GO+ Daily Earnings
    if re.match(r"^GO\+\s*Daily\s+Earnings", cleaned, flags=re.IGNORECASE):
        return "GO+ Daily Earnings"
    # Grab* A-A2UIAIGWWBIAAV -> Grab
    if re.match(r"^Grab\*\s+[A-Za-z0-9-]+$", cleaned, flags=re.IGNORECASE):
        return "Grab"
    # DuitNow QR TNGOW3MY1 059 ... / Payment TNGOW3MY1 941 711
    cleaned = re.sub(
        r"^(?:DuitNow\s+QR|Payment)\s+[A-Z0-9]+\s+\d+\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    # DUITNOW_TRANS FERTO 0118 LIM SHIAW YONG -> LIM SHIAW YONG
    cleaned = re.sub(
        r"^DUITNOW[_\s]+(?:TRANS\s*FERTO|RECEI\s*VEFROM)\s+\d+\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    # Transfer to Wallet 7639 ... / Receive from Wallet20261102111 1771 ...
    cleaned = re.sub(
        r"^(?:Transfer\s+to\s+Wallet|Receive\s+from\s+Wallet)\s*\d+(?:\s+\d+)*\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    # Card processor prefix asterisks like MICROSOFT*PC GAME PASS -> MICROSOFT PC GAME PASS
    cleaned = re.sub(r"(?<=[A-Za-z])\*(?=[A-Za-z0-9])", " ", cleaned)
    # Strip trailing 4-6 digit TNG/QR terminal IDs
    cleaned = re.sub(r"\s+\d{4,6}$", "", cleaned).strip()
    return cleaned or raw

