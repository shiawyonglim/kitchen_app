from datetime import datetime, timezone
from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WhitelistedSender(Base):
    """Approved bank, e-wallet, or receipt senders for fast targeted inbox sync."""

    __tablename__ = "whitelisted_senders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sender_email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category_hint: Mapped[str] = mapped_column(String(64), default="General", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class LedgerEntry(Base):
    """Immutable financial transaction extracted from inbox or manual freelance entry."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount_myr > 0", name="ck_ledger_amount_positive"),
        CheckConstraint("tx_type IN ('expense', 'income', 'repayment')", name="ck_ledger_tx_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    idempotency_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    sender_email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    merchant: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    tx_type: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    amount_myr: Mapped[float] = mapped_column(Float, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    subject: Mapped[str] = mapped_column(Text, default="", nullable=False)
    account_name: Mapped[str] = mapped_column(String(64), default="Other / Cash", index=True, nullable=False)
    is_self_transfer: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    is_claimable: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    remark: Mapped[str] = mapped_column(Text, default="", nullable=False)
    conflict_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    linked_entry_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class DeletionLog(Base):
    """Audit log of deleted ledger entries along with the user's remark."""

    __tablename__ = "deletion_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    original_entry_id: Mapped[int] = mapped_column(nullable=False)
    merchant: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    account_name: Mapped[str] = mapped_column(String(64), nullable=False)
    tx_type: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_myr: Mapped[float] = mapped_column(Float, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    remark: Mapped[str] = mapped_column(Text, default="", nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class BankAccount(Base):
    """Individually tracked user bank or e-wallet account with opening balance."""

    __tablename__ = "bank_accounts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    opening_balance_myr: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class CategoryRule(Base):
    """User-defined category and keyword rules for automatic transaction tagging."""

    __tablename__ = "category_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    keywords: Mapped[str] = mapped_column(Text, default="", nullable=False)
    flow_type: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)
    monthly_budget_myr: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class CategoryModelState(Base):
    """Persisted Naive Bayes category learning model trained on user edits."""

    __tablename__ = "category_model_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    manual_sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    categories_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    model_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RecurringBill(Base):
    """User-defined monthly recurring bill or subscription tracked against ledger expenses."""

    __tablename__ = "recurring_bills"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="Subscriptions & Digital", nullable=False)
    amount_myr: Mapped[float] = mapped_column(Float, nullable=False)
    due_day: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    frequency: Mapped[str] = mapped_column(String(20), default="monthly", nullable=False)
    interval_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    match_keywords: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

