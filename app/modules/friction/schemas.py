from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, Field


class LogOdometerRequest(BaseModel):
    vehicle: str = Field(default="Toyota Altis", max_length=64)
    odometer_km: float = Field(..., ge=0)
    notes: str = ""


class OdometerOut(BaseModel):
    id: int
    vehicle: str
    odometer_km: float
    notes: str
    logged_at: datetime

    model_config = {"from_attributes": True}


class LogUsageRequest(BaseModel):
    amount: float = Field(..., gt=0)


class GearOut(BaseModel):
    id: int
    name: str
    category: str
    unit: str
    used_units: float
    max_units: float
    remaining_units: float
    health_pct: float
    alert: bool


class CreateDebtRequest(BaseModel):
    event_name: str = Field(default="San Guo Sha Night", max_length=255)
    borrower_name: str = Field(..., min_length=1, max_length=128)
    amount_myr: float = Field(..., gt=0)
    direction: Literal["owed_to_me", "i_owe"] = "owed_to_me"
    monthly_installment_myr: float | None = Field(default=None, gt=0)
    match_keywords: str = Field(default="", max_length=255)
    start_date: date | None = None
    due_day: int | None = Field(default=None, ge=1, le=31)
    match_installment_only: bool = False
    interest_rate_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    interest_type: Literal["none", "flat", "reducing"] = "none"
    tenure_months: int | None = Field(default=None, ge=1, le=600)


class UpdateDebtRequest(BaseModel):
    event_name: str | None = Field(default=None, min_length=1, max_length=255)
    borrower_name: str | None = Field(default=None, min_length=1, max_length=128)
    amount_myr: float | None = Field(default=None, gt=0)
    direction: Literal["owed_to_me", "i_owe"] | None = None
    monthly_installment_myr: float | None = Field(default=None, gt=0)
    match_keywords: str | None = Field(default=None, max_length=255)
    start_date: date | None = None
    due_day: int | None = Field(default=None, ge=1, le=31)
    match_installment_only: bool | None = None
    interest_rate_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    interest_type: Literal["none", "flat", "reducing"] | None = None
    tenure_months: int | None = Field(default=None, ge=1, le=600)


class ExcludeDebtEntryRequest(BaseModel):
    entry_id: int


class AmortizationRowOut(BaseModel):
    month: int
    payment_myr: float
    principal_myr: float
    interest_myr: float
    balance_myr: float


class LoanCalculationRequest(BaseModel):
    principal_myr: float = Field(..., gt=0)
    annual_rate_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    tenure_months: int = Field(..., ge=1, le=600)
    interest_type: Literal["none", "flat", "reducing"] = "reducing"


class LoanCalculationOut(BaseModel):
    principal_myr: float
    annual_rate_pct: float
    tenure_months: int
    interest_type: str
    monthly_installment_myr: float
    total_interest_myr: float
    total_payable_myr: float
    effective_apr_pct: float
    schedule: list[AmortizationRowOut]


class PayDebtRequest(BaseModel):
    amount_myr: float = Field(..., gt=0)


class DebtPaymentMatchOut(BaseModel):
    entry_id: int
    occurred_at: datetime
    merchant: str
    amount_myr: float


class ParseTngSplitRequest(BaseModel):
    event_name: str = Field(default="San Guo Sha Night", max_length=255)
    raw_text: str = Field(..., min_length=3)


class SocialDebtOut(BaseModel):
    id: int
    event_name: str
    borrower_name: str
    amount_myr: float
    principal_myr: float = 0.0
    total_interest_myr: float = 0.0
    total_payable_myr: float = 0.0
    direction: str = "owed_to_me"
    paid_myr: float = 0.0
    principal_paid_myr: float = 0.0
    interest_paid_myr: float = 0.0
    remaining_myr: float = 0.0
    progress_pct: float = 0.0
    monthly_installment_myr: float | None = None
    months_left: int | None = None
    estimated_payoff_date: str | None = None
    paid_this_month: bool = False
    start_date: date | None = None
    due_day: int | None = None
    match_installment_only: bool = False
    interest_rate_pct: float = 0.0
    interest_type: str = "none"
    tenure_months: int | None = None
    match_keywords: str = ""
    auto_matched_payments: list[DebtPaymentMatchOut] = []
    amortization_schedule: list[AmortizationRowOut] = []
    settled: bool
    settled_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AltisCostSummary(BaseModel):
    vehicle: str
    total_fuel_myr: float
    min_odometer_km: float | None
    max_odometer_km: float | None
    distance_km: float
    cost_per_km_myr: float | None


class FrictionSummary(BaseModel):
    altis: AltisCostSummary
    odometer_logs: list[OdometerOut]
    gear: list[GearOut]
    unsettled_debts: list[SocialDebtOut]
    settled_debts: list[SocialDebtOut]
    total_owed_myr: float
    total_i_owe_myr: float = 0.0

