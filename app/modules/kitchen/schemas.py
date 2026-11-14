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


class ScanReceiptOut(BaseModel):
    extracted_lines: list[str]
    added_items: list[KitchenItemOut]

