from datetime import date, datetime, timezone
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, get_current_user_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OdometerLog(Base):
    """Toyota Altis odometer reading used to compute Setel fuel cost per km."""

    __tablename__ = "odometer_logs"
    __tenant_scoped__ = True
    __table_args__ = (
        CheckConstraint("odometer_km >= 0", name="ck_odometer_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    vehicle: Mapped[str] = mapped_column(String(64), default="Toyota Altis", nullable=False)
    odometer_km: Mapped[float] = mapped_column(Float, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)


class GearConsumable(Base):
    """Tracks consumable inventory (Car Shampoo) and wear thresholds (Yonex Arcsaber 11 Pro strings)."""

    __tablename__ = "gear_consumables"
    __tenant_scoped__ = True
    __table_args__ = (
        CheckConstraint("max_units > 0", name="ck_gear_max_positive"),
        CheckConstraint("used_units >= 0", name="ck_gear_used_nonneg"),
        UniqueConstraint("user_id", "name", name="uq_gear_consumables_user_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. "washes", "court hrs"
    used_units: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    max_units: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class SocialDebt(Base):
    """Friend IOUs from San Guo Sha nights or TNG split bills."""

    __tablename__ = "social_debts"
    __tenant_scoped__ = True
    __table_args__ = (
        CheckConstraint("amount_myr > 0", name="ck_debt_amount_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    event_name: Mapped[str] = mapped_column(String(255), default="San Guo Sha Night", nullable=False)
    borrower_name: Mapped[str] = mapped_column(String(128), nullable=False)
    amount_myr: Mapped[float] = mapped_column(Float, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), default="owed_to_me", nullable=False)
    manual_paid_myr: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    monthly_installment_myr: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_keywords: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_installment_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    excluded_entry_ids: Mapped[str] = mapped_column(Text, default="", nullable=False)
    interest_rate_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    interest_type: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    tenure_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    settled: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

