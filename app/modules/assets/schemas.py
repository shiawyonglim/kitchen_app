from datetime import datetime
from pydantic import BaseModel, Field


class CreateAssetRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    search_query: str = Field(..., min_length=2, max_length=255)
    category: str = Field(default="Hardware", max_length=64)
    condition: str = Field(default="Used", max_length=64)
    purchase_price_myr: float = Field(default=0.0, ge=0)
    quantity: int = Field(default=1, ge=1)


class ValuationOut(BaseModel):
    id: int
    median_price_myr: float
    min_price_myr: float
    max_price_myr: float
    sample_count: int
    source: str
    scraped_at: datetime

    model_config = {"from_attributes": True}


class AssetOut(BaseModel):
    id: int
    name: str
    search_query: str
    category: str
    condition: str
    purchase_price_myr: float
    quantity: int
    latest_valuation: ValuationOut | None = None
    total_liquid_value_myr: float
    unrealized_pnl_myr: float


class ParseListingsRequest(BaseModel):
    raw_text: str = Field(..., min_length=2)


class NetWorthSummary(BaseModel):
    liquid_cash_myr: float
    tracked_assets_value_myr: float
    total_net_worth_myr: float
    asset_count: int

