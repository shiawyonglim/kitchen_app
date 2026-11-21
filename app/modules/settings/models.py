from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, get_current_user_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AppSettings(Base):
    """Per-user configuration for daily auto-sync, budgets, and nutrition defaults."""

    __tablename__ = "app_settings"
    __tenant_scoped__ = True

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False, default=get_current_user_id)
    auto_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    live_listener_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_learn_categories: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_sync_hour: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    auto_sync_lookback_days: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    monthly_budget_myr: Mapped[float] = mapped_column(Float, default=3000.0, nullable=False)
    default_calories: Mapped[int] = mapped_column(Integer, default=2000, nullable=False)
    default_protein_g: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    account_holder_names: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    tng_pdf_password: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    visible_tabs: Mapped[str] = mapped_column(
        String(255),
        default="wealth,assets,kitchen,trainer,friction,assistant,resolve",
        nullable=False,
    )
    last_auto_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_auto_sync_status: Mapped[str] = mapped_column(String(255), default="Not run yet", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    @property
    def has_tng_pdf_password(self) -> bool:
        return bool(self.tng_pdf_password)


class AiUsageLog(Base):
    """Per-user telemetry log for AI provider & service calls."""

    __tablename__ = "ai_usage_logs"
    __tenant_scoped__ = True

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    provider: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    service: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ok", nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_detail: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)


class AiModelConfig(Base):
    """Per-user editable rate-limit configuration for each AI model."""

    __tablename__ = "ai_model_configs"
    __tenant_scoped__ = True
    __table_args__ = (
        UniqueConstraint("user_id", "provider", "model", name="uq_ai_model_configs_user_prov_model"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    limit_period: Mapped[str] = mapped_column(String(16), default="day", nullable=False)
    req_limit: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    tok_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)



