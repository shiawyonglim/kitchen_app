from datetime import datetime, timezone
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, get_current_user_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AssistantItem(Base):
    """Structured task, schedule, idea, or routed note parsed from WhatsApp or manual input."""

    __tablename__ = "assistant_items"
    __tenant_scoped__ = True

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    source: Mapped[str] = mapped_column(String(64), default="manual", nullable=False)
    sender: Mapped[str] = mapped_column(String(128), default="Me", nullable=False)
    raw_message: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="task", nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    ai_plan: Mapped[str] = mapped_column(Text, default="", nullable=False)
    scheduled_for: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    routed_to: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    engine: Mapped[str] = mapped_column(String(48), default="heuristic", nullable=False)
    cost_tier: Mapped[str] = mapped_column(String(16), default="low_cost", nullable=False)
    attachment_data_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
