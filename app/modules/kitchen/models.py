from datetime import date, datetime, timezone
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KitchenItem(Base):
    """Perishable or pantry item with shelf-life countdown."""

    __tablename__ = "kitchen_inventory"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_kitchen_qty_nonneg"),
        CheckConstraint("shelf_life_days > 0", name="ck_kitchen_shelf_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="Produce", nullable=False)
    quantity: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), default="pcs", nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="Manual", nullable=False)
    shelf_life_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    protein_g: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    calories: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class NutritionTarget(Base):
    """Daily dinner macro target dynamically adjusted by the Physical Trainer module."""

    __tablename__ = "nutrition_targets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    target_date: Mapped[date] = mapped_column(Date, unique=True, index=True, nullable=False)
    base_calories: Mapped[int] = mapped_column(Integer, default=2000, nullable=False)
    base_protein_g: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    bonus_calories: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bonus_protein_g: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    recovery_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_workout_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

