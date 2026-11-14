from datetime import datetime
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


class ParseTngSplitRequest(BaseModel):
    event_name: str = Field(default="San Guo Sha Night", max_length=255)
    raw_text: str = Field(..., min_length=3)


class SocialDebtOut(BaseModel):
    id: int
    event_name: str
    borrower_name: str
    amount_myr: float
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

