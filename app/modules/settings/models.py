from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AppSettings(Base):
    """Singleton configuration for daily auto-sync, budgets, and nutrition defaults."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    auto_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    live_listener_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_learn_categories: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_sync_hour: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    auto_sync_lookback_days: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    monthly_budget_myr: Mapped[float] = mapped_column(Float, default=3000.0, nullable=False)
    default_calories: Mapped[int] = mapped_column(Integer, default=2000, nullable=False)
    default_protein_g: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    account_holder_names: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    last_auto_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_auto_sync_status: Mapped[str] = mapped_column(String(255), default="Not run yet", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

