from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TrackedAsset(Base):
    """Physical hardware or collectible tracked for liquid cash net worth."""

    __tablename__ = "tracked_assets"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_asset_quantity_positive"),
        CheckConstraint("purchase_price_myr >= 0", name="ck_asset_purchase_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    search_query: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="Collectibles", nullable=False)
    condition: Mapped[str] = mapped_column(String(64), default="Used", nullable=False)
    purchase_price_myr: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    valuations: Mapped[list["AssetValuation"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", order_by="AssetValuation.scraped_at.desc()"
    )


class AssetValuation(Base):
    """Historical Carousell market valuation snapshot for a tracked asset."""

    __tablename__ = "asset_valuations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("tracked_assets.id", ondelete="CASCADE"), index=True)
    median_price_myr: Mapped[float] = mapped_column(Float, nullable=False)
    min_price_myr: Mapped[float] = mapped_column(Float, nullable=False)
    max_price_myr: Mapped[float] = mapped_column(Float, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_listings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)

    asset: Mapped[TrackedAsset] = relationship(back_populates="valuations")

