from datetime import datetime
from typing import Literal
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
    visible_tabs: str = "wealth,assets,kitchen,trainer,friction,assistant,resolve"
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
    visible_tabs: str | None = Field(default=None, max_length=255)


class AiProviderUsageOut(BaseModel):
    provider: str
    label: str
    configured: bool
    calls: int
    today_calls: int
    success_calls: int
    rate_limited_calls: int
    error_calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    today_tokens: int
    models: list[str]
    last_used_at: datetime | None = None
    last_status: str = ""


class AiServiceUsageOut(BaseModel):
    service: str
    label: str
    calls: int
    success_calls: int
    rate_limited_calls: int
    error_calls: int
    total_tokens: int
    avg_latency_ms: int
    last_model: str = ""
    last_used_at: datetime | None = None


class AiUsageLogOut(BaseModel):
    id: int
    provider: str
    model: str
    service: str
    status: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: int
    error_detail: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AiModelQuotaOut(BaseModel):
    provider: str
    provider_label: str
    model: str
    capabilities: list[str]
    configured: bool
    limit_period: Literal["minute", "day"]
    used_calls: int
    req_limit: int
    used_tokens: int
    tok_limit: int
    remaining_calls: int | None = None
    remaining_pct: float
    reset_in_seconds: int
    last_status: str = ""
    last_error: str = ""
    last_used_at: datetime | None = None


class UpdateAiModelConfigRequest(BaseModel):
    provider: str = Field(..., min_length=1, max_length=32)
    model: str = Field(..., min_length=1, max_length=128)
    limit_period: Literal["minute", "day"] = "day"
    req_limit: int = Field(default=100, ge=0, le=1_000_000)
    tok_limit: int = Field(default=0, ge=0, le=100_000_000)


class TestAiModelRequest(BaseModel):
    provider: str = Field(..., min_length=1, max_length=32)
    model: str = Field(..., min_length=1, max_length=128)


class TestAiModelResponse(BaseModel):
    provider: str
    model: str
    ok: bool
    status: str
    latency_ms: int
    tokens: int = 0
    detail: str = ""


class AiUsageSummaryOut(BaseModel):
    days: int
    total_calls: int
    today_calls: int
    success_calls: int
    rate_limited_calls: int
    error_calls: int
    total_tokens: int
    today_tokens: int
    providers: list[AiProviderUsageOut]
    services: list[AiServiceUsageOut]
    models: list[AiModelQuotaOut] = Field(default_factory=list)
    recent_logs: list[AiUsageLogOut]


