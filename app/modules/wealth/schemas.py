from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, Field


class DateRangeRequest(BaseModel):
    start_date: date
    end_date: date


class DiscoveredSender(BaseModel):
    sender_email: str
    display_name: str
    email_count: int
    sample_subject: str
    is_money_related: bool
    money_score: int
    is_whitelisted: bool
    category_hint: str


class SenderWhitelistItem(BaseModel):
    sender_email: str = Field(..., min_length=3, max_length=255)
    display_name: str = Field(..., min_length=1, max_length=255)
    category_hint: str = Field(default="General", max_length=64)
    received_count: int = 0


class SaveWhitelistRequest(BaseModel):
    senders: list[SenderWhitelistItem]


class SyncRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    account_name: str | None = None


class ManualLedgerRequest(BaseModel):
    merchant: str = Field(..., min_length=1, max_length=255)
    category: str = Field(default="General", max_length=64)
    tx_type: Literal["expense", "income", "repayment"] = "income"
    amount_myr: float = Field(..., gt=0)
    occurred_at: datetime
    account_name: str = Field(default="Other / Cash", max_length=64)
    notes: str = ""
    recurrence_mode: Literal["one_off", "monthly", "weekly", "daily", "yearly", "custom_days"] = "one_off"
    interval_days: int = Field(default=30, ge=1, le=3650)


class CategoryRuleUpsertRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    keywords: str = Field(default="", max_length=1000)
    flow_type: Literal["auto", "income", "repayment", "expense"] = "auto"
    monthly_budget_myr: float | None = Field(default=None, ge=0)
    color: str = Field(default="", pattern=r"^(#[0-9a-fA-F]{6})?$")
    day_min: int | None = Field(default=None, ge=1, le=31)
    day_max: int | None = Field(default=None, ge=1, le=31)
    amount_min: float | None = Field(default=None, ge=0)
    amount_max: float | None = Field(default=None, ge=0)
    tx_direction: Literal["any", "expense", "income"] = "any"
    account_filter: str = Field(default="", max_length=64)


class CategoryRuleOut(BaseModel):
    id: int
    name: str
    keywords: str
    flow_type: str = "auto"
    monthly_budget_myr: float | None = None
    color: str = ""
    day_min: int | None = None
    day_max: int | None = None
    amount_min: float | None = None
    amount_max: float | None = None
    tx_direction: str = "any"
    account_filter: str = ""

    model_config = {"from_attributes": True}


class LedgerEntryOut(BaseModel):
    id: int
    sender_email: str
    merchant: str
    display_merchant: str = ""
    category: str
    tx_type: str
    amount_myr: float
    occurred_at: datetime
    subject: str
    account_name: str = "Other / Cash"
    is_self_transfer: bool = False
    is_claimable: bool = False
    remark: str = ""
    linked_entry_id: int | None = None
    linked_repaid_myr: float = 0.0
    net_amount_myr: float | None = None
    linked_merchant: str | None = None
    gmail_thread_id: str | None = None
    suggested_link_entry_id: int | None = None
    suggested_link_merchant: str | None = None
    suggested_link_amount_myr: float | None = None
    split_count: int | None = None
    owed_remaining_myr: float | None = None

    model_config = {"from_attributes": True}


class LinkRepaymentsRequest(BaseModel):
    repayment_ids: list[int] = []
    split_count: int | None = Field(default=None, ge=1, le=50)


class UpdateEntryRequest(BaseModel):
    category: str | None = Field(default=None, min_length=1, max_length=64)
    tx_type: Literal["expense", "income", "repayment"] | None = None
    is_claimable: bool | None = None
    is_self_transfer: bool | None = None
    remark: str | None = Field(default=None, max_length=500)
    split_count: int | None = Field(default=None, ge=1, le=50)


class DeletionLogOut(BaseModel):
    id: int
    original_entry_id: int
    merchant: str
    category: str
    account_name: str
    tx_type: str
    amount_myr: float
    occurred_at: datetime
    remark: str
    deleted_at: datetime

    model_config = {"from_attributes": True}


class ConflictGroupOut(BaseModel):
    group_key: str
    account_name: str
    merchant: str
    tx_type: str
    amount_myr: float
    entries: list[LedgerEntryOut]


class ResolveConflictRequest(BaseModel):
    keep_ids: list[int] = []
    delete_ids: list[int] = []


class BankAccountUpsertRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    opening_balance_myr: float = 0.0
    current_balance_myr: float | None = None


class BankAccountBalanceOut(BaseModel):
    id: int | None = None
    name: str
    opening_balance_myr: float
    total_in_myr: float
    total_out_myr: float
    current_balance_myr: float
    tx_count: int


class SyncResult(BaseModel):
    scanned_emails: int
    inserted_count: int
    skipped_duplicates: int
    unparsed_count: int
    whitelisted_senders_used: list[str]
    mode: str


class StatementBackfillResult(BaseModel):
    filename: str
    bank_name: str
    parsed_rows: int
    inserted_count: int
    skipped_duplicates: int


class DailyCashFlowPoint(BaseModel):
    date: str
    income_myr: float
    repayment_myr: float = 0.0
    expense_myr: float
    net_myr: float


class MonthlyCategoryPoint(BaseModel):
    month: str
    income_myr: float
    expense_myr: float
    net_myr: float
    categories: dict[str, float]


class TransferCounterpartyStat(BaseModel):
    name: str
    total_myr: float
    tx_count: int
    accounts: list[str] = []
    entry_ids: list[int] = []


class RecurringBillUpsertRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    amount_myr: float = Field(..., gt=0)
    category: str = Field(default="Subscriptions & Digital", max_length=64)
    due_day: int = Field(default=1, ge=1, le=31)
    frequency: Literal["monthly", "weekly", "daily", "yearly", "custom_days"] = "monthly"
    interval_days: int = Field(default=30, ge=1, le=3650)
    match_keywords: str = Field(default="", max_length=500)


class RecurringBillOut(BaseModel):
    id: int
    name: str
    category: str
    amount_myr: float
    due_day: int = 1
    frequency: str = "monthly"
    interval_days: int = 30
    match_keywords: str = ""
    paid_this_month: bool = False
    paid_amount_myr: float = 0.0


class WealthSummary(BaseModel):
    total_income_myr: float
    total_repayment_myr: float = 0.0
    total_expense_myr: float
    net_cash_flow_myr: float
    rolling_30d_daily_burn_myr: float
    period_daily_burn_myr: float
    transaction_count: int
    daily_series: list[DailyCashFlowPoint]
    monthly_series: list[MonthlyCategoryPoint]
    category_breakdown: dict[str, float]
    account_balances: list[BankAccountBalanceOut] = []
    top_transfers_out: list[TransferCounterpartyStat] = []
    top_transfers_in: list[TransferCounterpartyStat] = []
    recurring_bills: list[RecurringBillOut] = []
    remaining_fixed_bills_myr: float = 0.0
    category_budgets: dict[str, float] = {}
    burn_pace_ratio: float | None = None


class SplitPartInput(BaseModel):
    amount_myr: float = Field(..., gt=0)
    merchant: str | None = Field(default=None, max_length=255)
    category: str | None = Field(default=None, max_length=64)
    tx_type: Literal["expense", "income", "repayment"] | None = None
    linked_entry_id: int | None = None


class SplitEntryRequest(BaseModel):
    parts: list[SplitPartInput] = Field(..., min_length=2, max_length=10)

