from datetime import datetime
from pydantic import BaseModel, Field


class AppSettingsOut(BaseModel):
    auto_sync_enabled: bool
    live_listener_enabled: bool
    auto_learn_categories: bool
    auto_sync_hour: int
    auto_sync_lookback_days: int
    monthly_budget_myr: float
    default_calories: int
    default_protein_g: int
    account_holder_names: str = ""
    has_tng_pdf_password: bool = False
    last_auto_sync_at: datetime | None
    last_auto_sync_status: str

    model_config = {"from_attributes": True}


class UpdateSettingsRequest(BaseModel):
    auto_sync_enabled: bool = True
    live_listener_enabled: bool = True
    auto_learn_categories: bool = True
    auto_sync_hour: int = Field(default=8, ge=0, le=23)
    auto_sync_lookback_days: int = Field(default=3, ge=1, le=30)
    monthly_budget_myr: float = Field(default=3000.0, gt=0)
    default_calories: int = Field(default=2000, ge=800, le=6000)
    default_protein_g: int = Field(default=120, ge=30, le=400)
    account_holder_names: str = Field(default="", max_length=500)
    tng_pdf_password: str | None = Field(default=None, max_length=100)

