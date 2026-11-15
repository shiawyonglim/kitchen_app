from datetime import date, datetime
from pydantic import BaseModel, Field


class AddKitchenItemRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    category: str = Field(default="Produce", max_length=64)
    quantity: float = Field(default=1.0, ge=0)
    unit: str = Field(default="pcs", max_length=32)
    source: str = Field(default="Manual", max_length=64)
    shelf_life_days: int = Field(default=7, ge=1)
    protein_g: float = Field(default=0.0, ge=0)
    calories: float = Field(default=0.0, ge=0)
    days_old: int = Field(default=0, ge=0)
    is_staple: bool = False


class IngestReceiptRequest(BaseModel):
    source: str = Field(default="NSK Thermal OCR", max_length=64)
    raw_text: str = Field(..., min_length=3)


class KitchenItemOut(BaseModel):
    id: int
    name: str
    category: str
    quantity: float
    unit: str
    source: str
    shelf_life_days: int
    days_elapsed: int
    days_remaining: int
    is_expiring_soon: bool
    protein_g: float
    calories: float
    is_staple: bool = False
    purchased_at: datetime


class NutritionTargetOut(BaseModel):
    target_date: date
    base_calories: int
    base_protein_g: int
    bonus_calories: int
    bonus_protein_g: int
    total_calories: int
    total_protein_g: int
    recovery_mode: bool
    last_workout_note: str
    recommended_additions: list[str]


class DinnerRecipeOut(BaseModel):
    title: str
    used_expiring_items: list[str]
    recovery_additions: list[str]
    instructions: str
    engine: str
    taste_notes: list[str] = []
    applied_rule_ids: list[int] = []
    ingredients_used: list[str] = []


class ScanReceiptOut(BaseModel):
    extracted_lines: list[str]
    added_items: list[KitchenItemOut]


class TasteProfileUpsert(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    sweet: float = Field(default=0.0, ge=0.0, le=1.0)
    sour: float = Field(default=0.0, ge=0.0, le=1.0)
    salty: float = Field(default=0.0, ge=0.0, le=1.0)
    bitter: float = Field(default=0.0, ge=0.0, le=1.0)
    umami: float = Field(default=0.0, ge=0.0, le=1.0)
    spicy: float = Field(default=0.0, ge=0.0, le=1.0)
    richness: float = Field(default=0.0, ge=0.0, le=1.0)
    texture: str = Field(default="", max_length=64)
    aroma: str = Field(default="", max_length=64)
    cooking_method: str = Field(default="", max_length=64)
    notes: str = Field(default="", max_length=500)


class TasteProfileOut(BaseModel):
    id: int
    name: str
    sweet: float
    sour: float
    salty: float
    bitter: float
    umami: float
    spicy: float
    richness: float
    texture: str = ""
    aroma: str = ""
    cooking_method: str = ""
    notes: str
    updated_at: datetime


class TasteRuleCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=160)
    primary_taste: str = Field(..., min_length=2, max_length=32)
    pair_tastes: str = Field(..., min_length=2, max_length=128)
    rationale: str = Field(..., min_length=3, max_length=600)


class TasteRuleOut(BaseModel):
    id: int
    title: str
    primary_taste: str
    pair_tastes: str
    rationale: str
    weight: float
    times_applied: int
    created_at: datetime


class PalatePreferenceOut(BaseModel):
    sweet: float
    sour: float
    salty: float
    bitter: float
    umami: float
    spicy: float
    richness: float
    feedback_count: int


class AppliedRuleMatch(BaseModel):
    rule_id: int
    title: str
    rationale: str
    matched_ingredients: list[str]
    score: float


class PairAffinityOut(BaseModel):
    ingredient_a: str
    ingredient_b: str
    affinity_score: float
    times_paired: int


class TasteAnalysisOut(BaseModel):
    palate: PalatePreferenceOut
    applied_rules: list[AppliedRuleMatch]
    missing_balance_hints: list[str]
    matched_profiles: list[TasteProfileOut]
    top_pair_affinities: list[PairAffinityOut] = []


class TasteFeedbackRequest(BaseModel):
    dish_title: str = Field(..., min_length=1, max_length=255)
    ingredients_used: list[str] = []
    applied_rule_ids: list[int] = []
    verdict: str = Field(..., pattern="^(loved|balanced|too_spicy|needs_acid|too_salty|too_bland|too_sweet)$")
    notes: str = Field(default="", max_length=500)


class TasteFeedbackOut(BaseModel):
    id: int
    dish_title: str
    verdict: str
    notes: str
    palate: PalatePreferenceOut
    created_at: datetime


class TasteFeedbackLogOut(BaseModel):
    id: int
    dish_title: str
    ingredients_used: str
    verdict: str
    notes: str
    created_at: datetime


class AutoProfileOut(BaseModel):
    created_profiles: list[TasteProfileOut]
    engine: str


class CookedMealRequest(BaseModel):
    dish_title: str = Field(..., min_length=1, max_length=255)
    ingredients_used: list[str] = []
    deduct_qty: float = Field(default=1.0, gt=0)


class CookedMealOut(BaseModel):
    deducted_items: list[str]
    removed_items: list[str]
    skipped_staples: list[str]


class ShoppingListCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    reason: str = Field(default="", max_length=500)


class ShoppingListItemOut(BaseModel):
    id: int
    name: str
    reason: str
    is_checked: bool
    created_at: datetime

