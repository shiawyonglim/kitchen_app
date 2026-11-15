from datetime import date, datetime, timezone
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, get_current_user_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KitchenItem(Base):
    """Perishable or pantry item with shelf-life countdown."""

    __tablename__ = "kitchen_inventory"
    __tenant_scoped__ = True
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_kitchen_qty_nonneg"),
        CheckConstraint("shelf_life_days > 0", name="ck_kitchen_shelf_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="Produce", nullable=False)
    quantity: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), default="pcs", nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="Manual", nullable=False)
    shelf_life_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    protein_g: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    calories: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_staple: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class NutritionTarget(Base):
    """Daily dinner macro target dynamically adjusted by the Physical Trainer module."""

    __tablename__ = "nutrition_targets"
    __tenant_scoped__ = True
    __table_args__ = (
        UniqueConstraint("user_id", "target_date", name="uq_nutrition_targets_user_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    target_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    base_calories: Mapped[int] = mapped_column(Integer, default=2000, nullable=False)
    base_protein_g: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    bonus_calories: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bonus_protein_g: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    recovery_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_workout_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class IngredientTasteProfile(Base):
    """Flavor vector for an ingredient across core taste dimensions (0.0 - 1.0)."""

    __tablename__ = "kitchen_taste_profiles"
    __tenant_scoped__ = True
    __table_args__ = (
        UniqueConstraint("user_id", "name_key", name="uq_taste_profile_user_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    name_key: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    sweet: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sour: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    salty: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    bitter: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    umami: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    spicy: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    richness: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    texture: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    aroma: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    cooking_method: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class TasteRule(Base):
    """User-taught and learned culinary pairing/balancing rule."""

    __tablename__ = "kitchen_taste_rules"
    __tenant_scoped__ = True

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    primary_taste: Mapped[str] = mapped_column(String(32), nullable=False)
    pair_tastes: Mapped[str] = mapped_column(String(128), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    times_applied: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class PalatePreference(Base):
    """Learned personal taste weights adjusted from meal feedback."""

    __tablename__ = "kitchen_palate_preferences"
    __tenant_scoped__ = True
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_palate_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    sweet: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    sour: Mapped[float] = mapped_column(Float, default=0.6, nullable=False)
    salty: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    bitter: Mapped[float] = mapped_column(Float, default=0.3, nullable=False)
    umami: Mapped[float] = mapped_column(Float, default=0.7, nullable=False)
    spicy: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    richness: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    feedback_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class TasteFeedback(Base):
    """Meal rating & taste correction log used to tune the palate model."""

    __tablename__ = "kitchen_taste_feedback"
    __tenant_scoped__ = True

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    dish_title: Mapped[str] = mapped_column(String(255), nullable=False)
    ingredients_used: Mapped[str] = mapped_column(Text, default="", nullable=False)
    applied_rule_ids: Mapped[str] = mapped_column(Text, default="", nullable=False)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class IngredientPairAffinity(Base):
    """Learned affinity score between two specific ingredients from meal feedback."""

    __tablename__ = "kitchen_pair_affinities"
    __tenant_scoped__ = True
    __table_args__ = (
        UniqueConstraint("user_id", "ingredient_a", "ingredient_b", name="uq_pair_affinity_user_ab"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    ingredient_a: Mapped[str] = mapped_column(String(128), nullable=False)
    ingredient_b: Mapped[str] = mapped_column(String(128), nullable=False)
    affinity_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    times_paired: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class ShoppingListItem(Base):
    """1-click shopping list item generated from missing taste balance or manual entry."""

    __tablename__ = "kitchen_shopping_list"
    __tenant_scoped__ = True

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_checked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

