from __future__ import annotations

import re
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.friction.models import GearConsumable, OdometerLog, SocialDebt
from app.modules.friction.schemas import (
    AltisCostSummary,
    CreateDebtRequest,
    FrictionSummary,
    GearOut,
    LogOdometerRequest,
    OdometerOut,
    SocialDebtOut,
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


def create_debt(db: Session, req: CreateDebtRequest) -> SocialDebt:
    debt = SocialDebt(
        event_name=req.event_name,
        borrower_name=req.borrower_name.strip(),
        amount_myr=round(req.amount_myr, 2),
    )
    db.add(debt)
    db.commit()
    db.refresh(debt)
    return debt


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
    ledger_rows = list(db.scalars(select(LedgerEntry).where(LedgerEntry.tx_type == "expense")).all())
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
    unsettled = [SocialDebtOut.model_validate(d) for d in all_debts if not d.settled]
    settled = [SocialDebtOut.model_validate(d) for d in all_debts if d.settled]

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
        total_owed_myr=round(sum(d.amount_myr for d in unsettled), 2),
    )

