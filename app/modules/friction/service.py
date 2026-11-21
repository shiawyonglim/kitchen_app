from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.friction.models import GearConsumable, OdometerLog, SocialDebt
from app.modules.friction.schemas import (
    AltisCostSummary,
    AmortizationRowOut,
    CreateDebtRequest,
    DebtPaymentMatchOut,
    FrictionSummary,
    GearOut,
    LoanCalculationOut,
    LogOdometerRequest,
    OdometerOut,
    SocialDebtOut,
    UpdateDebtRequest,
)
from app.modules.wealth.models import LedgerEntry

_DEBT_LINE_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9 _\-]{0,40}?)\s*[:\-–]?\s*(?:RM|MYR)?\s*([0-9]+(?:\.[0-9]{1,2})?)\s*$",
    re.IGNORECASE,
)


def seed_default_gear(db: Session) -> None:
    existing = set(db.scalars(select(GearConsumable.name)).all())
    defaults = [
        GearConsumable(
            name="Car Wash Shampoo",
            category="Auto Care",
            unit="washes",
            used_units=4.0,
            max_units=20.0,
        ),
        GearConsumable(
            name="Yonex Arcsaber 11 Pro Strings",
            category="Badminton",
            unit="court hrs",
            used_units=18.0,
            max_units=25.0,
        ),
    ]
    added = False
    for item in defaults:
        if item.name not in existing:
            db.add(item)
            added = True
    if added:
        db.commit()


def log_odometer(db: Session, req: LogOdometerRequest) -> OdometerLog:
    row = OdometerLog(vehicle=req.vehicle, odometer_km=req.odometer_km, notes=req.notes)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def log_gear_usage(db: Session, gear_id: int, amount: float) -> GearOut:
    gear = db.get(GearConsumable, gear_id)
    if not gear:
        raise ValueError("Gear item not found")
    gear.used_units = round(gear.used_units + amount, 2)
    gear.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(gear)
    return _to_gear_out(gear)


def reset_gear(db: Session, gear_id: int) -> GearOut:
    """Reset counter after refilling car shampoo or restringing the Arcsaber 11 Pro."""
    gear = db.get(GearConsumable, gear_id)
    if not gear:
        raise ValueError("Gear item not found")
    gear.used_units = 0.0
    gear.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(gear)
    return _to_gear_out(gear)


def _to_gear_out(g: GearConsumable) -> GearOut:
    remaining = max(0.0, round(g.max_units - g.used_units, 2))
    pct = max(0.0, min(100.0, round((remaining / g.max_units) * 100.0, 1)))
    return GearOut(
        id=g.id,
        name=g.name,
        category=g.category,
        unit=g.unit,
        used_units=g.used_units,
        max_units=g.max_units,
        remaining_units=remaining,
        health_pct=pct,
        alert=(pct <= 20.0),
    )


def calculate_loan(
    principal_myr: float,
    annual_rate_pct: float,
    tenure_months: int,
    interest_type: str = "reducing",
) -> LoanCalculationOut:
    """Compute monthly installment, total interest, effective APR, and amortization schedule."""
    p = round(float(principal_myr), 2)
    n = max(1, int(tenure_months))
    rate = max(0.0, float(annual_rate_pct))
    itype = interest_type if interest_type in ("none", "flat", "reducing") else "reducing"
    schedule: list[AmortizationRowOut] = []

    if itype == "none" or rate == 0.0:
        monthly = round(p / n, 2)
        bal = p
        for m in range(1, n + 1):
            pay = round(bal, 2) if m == n else monthly
            bal = max(0.0, round(bal - pay, 2))
            schedule.append(
                AmortizationRowOut(month=m, payment_myr=pay, principal_myr=pay, interest_myr=0.0, balance_myr=bal)
            )
        return LoanCalculationOut(
            principal_myr=p,
            annual_rate_pct=0.0,
            tenure_months=n,
            interest_type=itype,
            monthly_installment_myr=monthly,
            total_interest_myr=0.0,
            total_payable_myr=p,
            effective_apr_pct=0.0,
            schedule=schedule,
        )

    if itype == "flat":
        total_int = round(p * (rate / 100.0) * (n / 12.0), 2)
        total_pay = round(p + total_int, 2)
        monthly = round(total_pay / n, 2)
        int_per_mo = round(total_int / n, 2)
        prin_per_mo = round(monthly - int_per_mo, 2)
        bal = p
        for m in range(1, n + 1):
            if m == n:
                prin_m = round(bal, 2)
                int_m = round(max(0.0, total_int - int_per_mo * (n - 1)), 2)
                pay_m = round(prin_m + int_m, 2)
                bal = 0.0
            else:
                prin_m = prin_per_mo
                int_m = int_per_mo
                pay_m = monthly
                bal = max(0.0, round(bal - prin_m, 2))
            schedule.append(
                AmortizationRowOut(month=m, payment_myr=pay_m, principal_myr=prin_m, interest_myr=int_m, balance_myr=bal)
            )
        # Approximate effective APR for flat-rate loans (constant-ratio / Rule of 78s approximation)
        eff_apr = round((2.0 * n * rate) / (n + 1.0), 2) if n > 1 else round(rate, 2)
        return LoanCalculationOut(
            principal_myr=p,
            annual_rate_pct=rate,
            tenure_months=n,
            interest_type="flat",
            monthly_installment_myr=monthly,
            total_interest_myr=total_int,
            total_payable_myr=total_pay,
            effective_apr_pct=eff_apr,
            schedule=schedule,
        )

    # Reducing balance
    r = (rate / 100.0) / 12.0
    factor = math.pow(1.0 + r, n)
    monthly = round(p * (r * factor) / (factor - 1.0), 2)
    bal = p
    total_int = 0.0
    for m in range(1, n + 1):
        int_m = round(bal * r, 2)
        if m == n:
            prin_m = round(bal, 2)
            pay_m = round(prin_m + int_m, 2)
            bal = 0.0
        else:
            prin_m = round(monthly - int_m, 2)
            pay_m = monthly
            bal = max(0.0, round(bal - prin_m, 2))
        total_int = round(total_int + int_m, 2)
        schedule.append(
            AmortizationRowOut(month=m, payment_myr=pay_m, principal_myr=prin_m, interest_myr=int_m, balance_myr=bal)
        )
    total_pay = round(p + total_int, 2)
    return LoanCalculationOut(
        principal_myr=p,
        annual_rate_pct=rate,
        tenure_months=n,
        interest_type="reducing",
        monthly_installment_myr=monthly,
        total_interest_myr=total_int,
        total_payable_myr=total_pay,
        effective_apr_pct=round(rate, 2),
        schedule=schedule,
    )


def create_debt(db: Session, req: CreateDebtRequest) -> SocialDebtOut:
    name = req.borrower_name.strip()
    keywords = req.match_keywords.strip() or name.lower()
    monthly = req.monthly_installment_myr
    if monthly is None and req.tenure_months and req.interest_type != "none":
        calc = calculate_loan(req.amount_myr, req.interest_rate_pct, req.tenure_months, req.interest_type)
        monthly = calc.monthly_installment_myr

    debt = SocialDebt(
        event_name=req.event_name.strip(),
        borrower_name=name,
        amount_myr=round(req.amount_myr, 2),
        direction=req.direction,
        monthly_installment_myr=round(monthly, 2) if monthly else None,
        match_keywords=keywords,
        start_date=req.start_date,
        due_day=req.due_day,
        match_installment_only=req.match_installment_only,
        interest_rate_pct=round(req.interest_rate_pct, 3),
        interest_type=req.interest_type,
        tenure_months=req.tenure_months,
    )
    db.add(debt)
    db.commit()
    db.refresh(debt)
    ledger_rows = list(db.scalars(select(LedgerEntry)).all())
    out = _build_debt_out(db, debt, ledger_rows)
    db.commit()
    return out


def update_debt(db: Session, debt_id: int, req: UpdateDebtRequest) -> SocialDebtOut:
    debt = db.get(SocialDebt, debt_id)
    if not debt:
        raise ValueError("Debt not found")
    if req.event_name is not None:
        debt.event_name = req.event_name.strip()
    if req.borrower_name is not None:
        debt.borrower_name = req.borrower_name.strip()
    if req.amount_myr is not None:
        debt.amount_myr = round(req.amount_myr, 2)
    if req.direction is not None:
        debt.direction = req.direction
    if req.monthly_installment_myr is not None:
        debt.monthly_installment_myr = round(req.monthly_installment_myr, 2)
    if req.match_keywords is not None:
        debt.match_keywords = req.match_keywords.strip() or debt.borrower_name.lower()
    if req.start_date is not None:
        debt.start_date = req.start_date
    if req.due_day is not None:
        debt.due_day = req.due_day
    if req.match_installment_only is not None:
        debt.match_installment_only = req.match_installment_only
    if req.interest_rate_pct is not None:
        debt.interest_rate_pct = round(req.interest_rate_pct, 3)
    if req.interest_type is not None:
        debt.interest_type = req.interest_type
    if req.tenure_months is not None:
        debt.tenure_months = req.tenure_months
    db.commit()
    ledger_rows = list(db.scalars(select(LedgerEntry)).all())
    out = _build_debt_out(db, debt, ledger_rows)
    db.commit()
    return out


def exclude_debt_entry(db: Session, debt_id: int, entry_id: int) -> SocialDebtOut:
    debt = db.get(SocialDebt, debt_id)
    if not debt:
        raise ValueError("Debt not found")
    existing = {x.strip() for x in (debt.excluded_entry_ids or "").split(",") if x.strip()}
    existing.add(str(entry_id))
    debt.excluded_entry_ids = ",".join(sorted(existing))
    db.commit()
    ledger_rows = list(db.scalars(select(LedgerEntry)).all())
    out = _build_debt_out(db, debt, ledger_rows)
    db.commit()
    return out


def _add_months(base_date: date, months: int) -> date:
    y = base_date.year + (base_date.month - 1 + months) // 12
    m = (base_date.month - 1 + months) % 12 + 1
    d = min(base_date.day, 28)
    return date(y, m, d)


def _build_debt_out(db: Session, debt: SocialDebt, all_ledger_rows: list[LedgerEntry]) -> SocialDebtOut:
    """Compute partial repayments, interest/principal split, payoff countdown, and auto-matched transfers."""
    kw_raw = debt.match_keywords.strip() or debt.borrower_name.strip().lower()
    keywords = [k.strip().lower() for k in kw_raw.split(",") if k.strip()]
    effective_start = debt.start_date or (debt.created_at.date() if debt.created_at else date.today())
    excluded_ids = {int(x) for x in (debt.excluded_entry_ids or "").split(",") if x.strip().isdigit()}

    matched: list[DebtPaymentMatchOut] = []
    for r in all_ledger_rows:
        if r.id in excluded_ids or r.is_self_transfer:
            continue
        if debt.direction == "i_owe" and r.tx_type != "expense":
            continue
        if debt.direction != "i_owe" and r.tx_type not in ("repayment", "income"):
            continue
        if r.occurred_at and r.occurred_at.date() < effective_start:
            continue
        if debt.match_installment_only and debt.monthly_installment_myr:
            tol = max(1.0, debt.monthly_installment_myr * 0.05)
            if abs(r.amount_myr - debt.monthly_installment_myr) > tol:
                continue
        haystack = f"{r.merchant} {r.subject}".lower()
        if any(k in haystack for k in keywords):
            matched.append(
                DebtPaymentMatchOut(
                    entry_id=r.id,
                    occurred_at=r.occurred_at,
                    merchant=r.merchant,
                    amount_myr=round(r.amount_myr, 2),
                )
            )
    matched.sort(key=lambda m: m.occurred_at, reverse=True)

    # Loan interest & amortization calculation
    principal = round(debt.amount_myr, 2)
    schedule: list[AmortizationRowOut] = []
    total_interest = 0.0
    total_payable = principal
    est_monthly = debt.monthly_installment_myr

    if debt.interest_type in ("flat", "reducing") and (debt.interest_rate_pct or 0.0) > 0:
        n_months = debt.tenure_months
        if not n_months and est_monthly and est_monthly > 0:
            n_months = max(1, math.ceil(principal / est_monthly))
        if n_months:
            calc = calculate_loan(principal, debt.interest_rate_pct, n_months, debt.interest_type)
            total_interest = calc.total_interest_myr
            total_payable = calc.total_payable_myr
            schedule = calc.schedule
            if not est_monthly:
                est_monthly = calc.monthly_installment_myr

    auto_paid = sum(m.amount_myr for m in matched)
    raw_paid = round((debt.manual_paid_myr or 0.0) + auto_paid, 2)
    if debt.settled:
        paid_myr = round(total_payable, 2)
    else:
        paid_myr = round(min(total_payable, raw_paid), 2)
        if raw_paid >= total_payable - 0.01:
            debt.settled = True
            debt.settled_at = datetime.now(timezone.utc)

    remaining_myr = max(0.0, round(total_payable - paid_myr, 2))
    progress_pct = min(100.0, round((paid_myr / total_payable) * 100.0, 1)) if total_payable > 0 else 100.0

    # Allocate paid_myr across principal & interest
    if total_interest > 0 and total_payable > 0:
        interest_paid_myr = round(min(total_interest, paid_myr * (total_interest / total_payable)), 2)
        principal_paid_myr = round(max(0.0, paid_myr - interest_paid_myr), 2)
    else:
        interest_paid_myr = 0.0
        principal_paid_myr = paid_myr

    today = date.today()
    paid_this_month = any(
        m.occurred_at.year == today.year and m.occurred_at.month == today.month
        for m in matched
    )

    months_left: int | None = None
    estimated_payoff_date: str | None = None
    if remaining_myr <= 0:
        months_left = 0
        estimated_payoff_date = "Paid Off"
    elif est_monthly and est_monthly > 0:
        months_left = max(1, math.ceil(remaining_myr / est_monthly))
        estimated_payoff_date = _add_months(today, months_left).strftime("%b %Y")

    return SocialDebtOut(
        id=debt.id,
        event_name=debt.event_name,
        borrower_name=debt.borrower_name,
        amount_myr=debt.amount_myr,
        principal_myr=principal,
        total_interest_myr=total_interest,
        total_payable_myr=total_payable,
        direction=debt.direction,
        paid_myr=paid_myr,
        principal_paid_myr=principal_paid_myr,
        interest_paid_myr=interest_paid_myr,
        remaining_myr=remaining_myr,
        progress_pct=progress_pct,
        monthly_installment_myr=est_monthly,
        months_left=months_left,
        estimated_payoff_date=estimated_payoff_date,
        paid_this_month=paid_this_month,
        start_date=debt.start_date,
        due_day=debt.due_day,
        match_installment_only=debt.match_installment_only,
        interest_rate_pct=debt.interest_rate_pct,
        interest_type=debt.interest_type,
        tenure_months=debt.tenure_months,
        match_keywords=debt.match_keywords,
        auto_matched_payments=matched,
        amortization_schedule=schedule,
        settled=debt.settled,
        settled_at=debt.settled_at,
        created_at=debt.created_at,
    )


def pay_debt(db: Session, debt_id: int, amount_myr: float) -> SocialDebtOut:
    debt = db.get(SocialDebt, debt_id)
    if not debt:
        raise ValueError("Debt not found")
    debt.manual_paid_myr = round((debt.manual_paid_myr or 0.0) + amount_myr, 2)
    ledger_rows = list(db.scalars(select(LedgerEntry)).all())
    out = _build_debt_out(db, debt, ledger_rows)
    db.commit()
    return out


def parse_tng_split_bill(db: Session, event_name: str, raw_text: str) -> list[SocialDebt]:
    """Parse Touch 'n Go split-bill lines like 'Alex RM 24.50' into active debts."""
    created: list[SocialDebt] = []
    for line in raw_text.splitlines():
        m = _DEBT_LINE_RE.match(line)
        if not m:
            continue
        name = m.group(1).strip()
        amount = round(float(m.group(2)), 2)
        if not name or amount <= 0:
            continue
        debt = SocialDebt(event_name=event_name, borrower_name=name, amount_myr=amount)
        db.add(debt)
        created.append(debt)
    db.commit()
    for d in created:
        db.refresh(d)
    return created


def settle_debt(db: Session, debt_id: int) -> SocialDebt:
    debt = db.get(SocialDebt, debt_id)
    if not debt:
        raise ValueError("Debt not found")
    debt.settled = True
    debt.settled_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(debt)
    return debt


def delete_debt(db: Session, debt_id: int) -> bool:
    debt = db.get(SocialDebt, debt_id)
    if not debt:
        return False
    db.delete(debt)
    db.commit()
    return True


def delete_odometer(db: Session, odo_id: int) -> bool:
    row = db.get(OdometerLog, odo_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def get_summary(db: Session) -> FrictionSummary:
    # Altis cost per km: Setel/Fuel spend from Wealth Ledger / odometer delta
    all_ledger_rows = list(db.scalars(select(LedgerEntry)).all())
    ledger_rows = [r for r in all_ledger_rows if r.tx_type == "expense"]
    total_fuel = sum(
        r.amount_myr
        for r in ledger_rows
        if r.category.lower() == "fuel" or "setel" in r.merchant.lower() or "setel" in r.sender_email.lower()
    )
    odos = list(db.scalars(select(OdometerLog).order_by(OdometerLog.odometer_km.asc())).all())
    min_odo = odos[0].odometer_km if odos else None
    max_odo = odos[-1].odometer_km if odos else None
    dist = round(max_odo - min_odo, 2) if (min_odo is not None and max_odo is not None and max_odo > min_odo) else 0.0
    cost_per_km = round(total_fuel / dist, 4) if dist > 0 else None

    gear_rows = list(db.scalars(select(GearConsumable).order_by(GearConsumable.id)).all())
    all_debts = list(db.scalars(select(SocialDebt).order_by(SocialDebt.created_at.desc())).all())
    debt_outs = [_build_debt_out(db, d, all_ledger_rows) for d in all_debts]
    db.commit()
    unsettled = [d for d in debt_outs if not d.settled]
    settled = [d for d in debt_outs if d.settled]

    return FrictionSummary(
        altis=AltisCostSummary(
            vehicle="Toyota Altis",
            total_fuel_myr=round(total_fuel, 2),
            min_odometer_km=min_odo,
            max_odometer_km=max_odo,
            distance_km=dist,
            cost_per_km_myr=cost_per_km,
        ),
        odometer_logs=[OdometerOut.model_validate(o) for o in reversed(odos)],
        gear=[_to_gear_out(g) for g in gear_rows],
        unsettled_debts=unsettled,
        settled_debts=settled,
        total_owed_myr=round(sum(d.remaining_myr for d in unsettled if d.direction != "i_owe"), 2),
        total_i_owe_myr=round(sum(d.remaining_myr for d in unsettled if d.direction == "i_owe"), 2),
    )

