from datetime import datetime, timezone
from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OdometerLog(Base):
    """Toyota Altis odometer reading used to compute Setel fuel cost per km."""

    __tablename__ = "odometer_logs"
    __table_args__ = (
        CheckConstraint("odometer_km >= 0", name="ck_odometer_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    vehicle: Mapped[str] = mapped_column(String(64), default="Toyota Altis", nullable=False)
    odometer_km: Mapped[float] = mapped_column(Float, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)


class GearConsumable(Base):
    """Tracks consumable inventory (Car Shampoo) and wear thresholds (Yonex Arcsaber 11 Pro strings)."""

    __tablename__ = "gear_consumables"
    __table_args__ = (
        CheckConstraint("max_units > 0", name="ck_gear_max_positive"),
        CheckConstraint("used_units >= 0", name="ck_gear_used_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. "washes", "court hrs"
    used_units: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    max_units: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class SocialDebt(Base):
    """Friend IOUs from San Guo Sha nights or TNG split bills."""

    __tablename__ = "social_debts"
    __table_args__ = (
        CheckConstraint("amount_myr > 0", name="ck_debt_amount_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_name: Mapped[str] = mapped_column(String(255), default="San Guo Sha Night", nullable=False)
    borrower_name: Mapped[str] = mapped_column(String(128), nullable=False)
    amount_myr: Mapped[float] = mapped_column(Float, nullable=False)
    settled: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

