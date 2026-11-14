from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WorkoutLog(Base):
    """Completed strength or cardio session that feeds Kitchen recovery targets."""

    __tablename__ = "workout_logs"
    __table_args__ = (
        CheckConstraint("calories_burned >= 0", name="ck_workout_cal_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    exercise_type: Mapped[str] = mapped_column(String(64), nullable=False)
    reps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    distance_km: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    duration_min: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    calories_burned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    avg_velocity_mps: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sensor_source: Mapped[str] = mapped_column(String(64), default="Forma Optical-Inertial", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)

