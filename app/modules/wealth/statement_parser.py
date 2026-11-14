"""Bank statement parser (CSV & PDF) for idempotent Wealth Ledger backfill."""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pypdf import PdfReader

from app.modules.wealth.parser import infer_category_hint

_DATE_FORMATS = (
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y-%m-%d",
    "%d/%m/%y",
    "%d-%m-%y",
    "%d %b %Y",
    "%d %b %y",
)

_PDF_LINE_RE = re.compile(
    r"^\s*(\d{2}[/\-]\d{2}[/\-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4})\s+"
    r"(.+?)\s+([+\-]?(?:RM|MYR)?\s*[0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2}|[+\-]?(?:RM|MYR)?\s*[0-9]+\.[0-9]{2})"
    r"(?:\s*(DR|CR))?(?:\s+[0-9,]+\.[0-9]{2})?\s*$",
    re.IGNORECASE,
)

_SKIP_DESC_RE = re.compile(
    r"(?:opening\s+balance|closing\s+balance|brought\s+forward|carried\s+forward|balance\s+b/f|balance\s+c/f|total\s+debit|total\s+credit)",
    re.IGNORECASE,
)


class PdfLockedError(ValueError):
    """Raised when a PDF bank statement is encrypted and needs a password."""


@dataclass
class StatementTx:
    idempotency_hash: str
    bank_name: str
    merchant: str
    category: str
    tx_type: str
    amount_myr: float
    occurred_at: datetime
    description: str


def _parse_date(raw: str) -> datetime | None:
    cleaned = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _clean_amount(raw: str) -> float | None:
    s = re.sub(r"[^\d.\-+]", "", raw or "")
    if not s:
        return None
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _make_tx(bank_name: str, dt: datetime, desc: str, tx_type: str, amount: float) -> StatementTx:
    clean_desc = " ".join(desc.split())[:250]
    key = f"stmt|{bank_name.lower()}|{dt.date().isoformat()}|{clean_desc.lower()}|{tx_type}|{amount:.2f}"
    idem = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return StatementTx(
        idempotency_hash=idem,
        bank_name=bank_name,
        merchant=clean_desc[:80] or bank_name,
        category=infer_category_hint(bank_name, bank_name, clean_desc),
        tx_type=tx_type,
        amount_myr=abs(amount),
        occurred_at=dt,
        description=clean_desc,
    )


def parse_csv_statement(file_bytes: bytes, bank_name: str) -> list[StatementTx]:
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []

    fields = {f.strip().lower(): f for f in reader.fieldnames if f}
    date_col = next((fields[k] for k in fields if "date" in k), None)
    desc_col = next((fields[k] for k in fields if any(w in k for w in ("desc", "detail", "particular", "merchant", "narrative", "reference"))), None)
    debit_col = next((fields[k] for k in fields if any(w in k for w in ("debit", "withdraw", "out"))), None)
    credit_col = next((fields[k] for k in fields if any(w in k for w in ("credit", "deposit", "in"))), None)
    amt_col = next((fields[k] for k in fields if "amount" in k), None)

    if not date_col:
        return []

    results: list[StatementTx] = []
    for row in reader:
        dt = _parse_date(row.get(date_col, ""))
        if not dt:
            continue
        desc = (row.get(desc_col, "") if desc_col else "").strip() or bank_name
        if _SKIP_DESC_RE.search(desc):
            continue

        tx_type = "expense"
        amount: float | None = None

        if debit_col or credit_col:
            d_val = _clean_amount(row.get(debit_col, "")) if debit_col else None
            c_val = _clean_amount(row.get(credit_col, "")) if credit_col else None
            if c_val and abs(c_val) > 0:
                tx_type = "income"
                amount = abs(c_val)
            elif d_val and abs(d_val) > 0:
                tx_type = "expense"
                amount = abs(d_val)
        elif amt_col:
            val = _clean_amount(row.get(amt_col, ""))
            if val is not None and val != 0:
                tx_type = "income" if val > 0 else "expense"
                amount = abs(val)

        if amount and amount > 0:
            results.append(_make_tx(bank_name, dt, desc, tx_type, amount))
    return results


_TNG_ROW_START_RE = re.compile(
    r"^(\d{1,2}/\d{1,2}/\d{4})\s+([A-Za-z]+)\s+(.+)$"
)
_TNG_AMOUNTS_RE = re.compile(
    r"RM\s*([0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{2,4}|[0-9]+\.[0-9]{2,4})\s+RM\s*[0-9,]+\.[0-9]{2,4}\s*$"
)

_TNG_INCOME_TYPES = (
    "receive from wallet",
    "receive",
    "duitnow_recei",
    "refund",
    "cashback",
    "go+ cash in",
    "go+ daily earnings",
    "reload",
)


def _parse_tng_pdf_blocks(all_lines: list[str], bank_name: str) -> list[StatementTx]:
    """Parse multi-line wrapped rows from Touch 'n Go eWallet PDF statements."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in all_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("Date Status Transaction Type") or stripped.startswith("TNG WALLET"):
            continue
        if _TNG_ROW_START_RE.match(stripped):
            if current:
                blocks.append(current)
            current = [stripped]
        elif current:
            current.append(stripped)
    if current:
        blocks.append(current)

    results: list[StatementTx] = []
    for blk in blocks:
        first_m = _TNG_ROW_START_RE.match(blk[0])
        if not first_m:
            continue
        dt = _parse_date(first_m.group(1))
        status = first_m.group(2).lower()
        if not dt or status != "success":
            continue

        joined = " ".join(blk)
        amt_m = _TNG_AMOUNTS_RE.search(joined)
        if not amt_m:
            continue
        amount = round(float(amt_m.group(1).replace(",", "")), 2)
        if amount <= 0:
            continue

        rest = first_m.group(3)
        # Match known multi-word types (including glued GO+ Daily EarningsMMF...) or ref ID boundary
        known_m = re.match(
            r"^(GO\+ Daily Earnings|GO\+ Cash In|Transfer to Wallet|Receive from Wallet|PayDirect Payment|"
            r"DuitNow QR TNGD|DuitNow QR|eWallet Cash Out|Payment Cancelled|DUITNOW_RECEI(?:VED)?|"
            r"DUITNOW_TRANS(?:FER)?|Payment|Reload|Receive|Refund)",
            rest,
            re.IGNORECASE,
        )
        if known_m:
            tng_tx_type = known_m.group(1).strip()
        else:
            tx_type_m = re.match(r"^([A-Za-z0-9 +/\-_]+?)\s+(?:\d{8,}|[A-Z]{2,}\d{6,})", rest)
            tng_tx_type = tx_type_m.group(1).strip() if tx_type_m else rest.split()[0]
        tng_type_lower = tng_tx_type.lower()

        if "cancelled" in tng_type_lower:
            continue
        joined_lower = joined.lower()
        # Skip internal TNG eWallet <-> GO+ transfers (they log paired Cash Out + Cash In rows)
        if "via ewallet to go+" in joined_lower and tng_type_lower in ("ewallet cash out", "go+ cash in"):
            continue
        if tng_type_lower == "reload" and "via go+" in joined_lower:
            continue

        if any(k in tng_type_lower for k in ("receive from wallet", "duitnow_recei")):
            tx_type = "repayment"
        elif any(k in tng_type_lower for k in _TNG_INCOME_TYPES):
            tx_type = "income"
        else:
            tx_type = "expense"

        # Clean merchant/description by stripping leading header, long ref IDs, and trailing RM balances
        desc_part = _TNG_AMOUNTS_RE.sub("", joined)
        desc_part = _TNG_ROW_START_RE.sub(r"\3", desc_part)
        desc_cleaned = re.sub(r"\b[A-Z0-9]{10,}\b", " ", desc_part)
        desc_cleaned = re.sub(r"\b\d{6,}\b", " ", desc_cleaned)
        desc_cleaned = " ".join(desc_cleaned.split()).strip()
        if not desc_cleaned:
            desc_cleaned = tng_tx_type

        cat = "Parking & E-Wallet" if "paydirect" in tng_type_lower or "parking" in desc_cleaned.lower() else infer_category_hint(bank_name, bank_name, desc_cleaned)
        # Include full raw block hash salt so same-day identical RM4.90 transactions remain distinct
        uniq_key = f"tngpdf|{dt.date().isoformat()}|{' '.join(blk)}"
        idem = hashlib.sha256(uniq_key.encode("utf-8")).hexdigest()
        results.append(
            StatementTx(
                idempotency_hash=idem,
                bank_name=bank_name,
                merchant=desc_cleaned[:80],
                category=cat,
                tx_type=tx_type,
                amount_myr=amount,
                occurred_at=dt,
                description=f"{tng_tx_type}: {desc_cleaned}"[:250],
            )
        )
    return results


def parse_pdf_statement(
    file_bytes: bytes, bank_name: str, pdf_password: str | None = None
) -> list[StatementTx]:
    reader = PdfReader(io.BytesIO(file_bytes))
    if reader.is_encrypted:
        if not pdf_password:
            raise PdfLockedError("PDF is password-protected. Please enter the PDF password to unlock.")
        if reader.decrypt(pdf_password) == 0:
            raise PdfLockedError("Incorrect PDF password. Please check and try again.")

    all_lines: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        all_lines.extend(text.splitlines())

    if any("TNG WALLET TRANSACTION" in line for line in all_lines[:40]):
        return _parse_tng_pdf_blocks(all_lines, bank_name)

    results: list[StatementTx] = []
    for line in all_lines:
        m = _PDF_LINE_RE.match(line)
        if not m:
            continue
        dt = _parse_date(m.group(1))
        if not dt:
            continue
        desc = m.group(2).strip()
        if _SKIP_DESC_RE.search(desc):
            continue
        raw_amt = m.group(3)
        val = _clean_amount(raw_amt)
        if val is None or val == 0:
            continue

        dr_cr = (m.group(4) or "").upper()
        if dr_cr == "CR" or raw_amt.strip().startswith("+"):
            tx_type = "income"
        elif dr_cr == "DR" or raw_amt.strip().startswith("-"):
            tx_type = "expense"
        else:
            lower_desc = desc.lower()
            tx_type = (
                "income"
                if any(w in lower_desc for w in ("credit", "deposit", "salary", "received", "interest", "refund", "payout"))
                else "expense"
            )
        results.append(_make_tx(bank_name, dt, desc, tx_type, abs(val)))
    return results


def parse_statement_file(
    filename: str, file_bytes: bytes, bank_name: str, pdf_password: str | None = None
) -> list[StatementTx]:
    lower = filename.lower()
    if lower.endswith(".csv"):
        return parse_csv_statement(file_bytes, bank_name)
    if lower.endswith(".pdf"):
        return parse_pdf_statement(file_bytes, bank_name, pdf_password=pdf_password)
    raise ValueError("Unsupported file format. Please upload a .csv or .pdf bank statement.")

