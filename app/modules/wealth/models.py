from datetime import datetime, timezone
from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, get_current_user_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WhitelistedSender(Base):
    """Approved bank, e-wallet, or receipt senders for fast targeted inbox sync."""

    __tablename__ = "whitelisted_senders"
    __tenant_scoped__ = True
    __table_args__ = (
        UniqueConstraint("user_id", "sender_email", name="uq_whitelisted_senders_user_email"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    sender_email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category_hint: Mapped[str] = mapped_column(String(64), default="General", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class LedgerEntry(Base):
    """Immutable financial transaction extracted from inbox or manual freelance entry."""

    __tablename__ = "ledger_entries"
    __tenant_scoped__ = True
    __table_args__ = (
        CheckConstraint("amount_myr > 0", name="ck_ledger_amount_positive"),
        CheckConstraint("tx_type IN ('expense', 'income', 'repayment')", name="ck_ledger_tx_type"),
        UniqueConstraint("user_id", "idempotency_hash", name="uq_ledger_entries_user_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    idempotency_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
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
    __tenant_scoped__ = True

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
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
    __tenant_scoped__ = True
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_bank_accounts_user_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    opening_balance_myr: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class CategoryRule(Base):
    """User-defined category and keyword rules for automatic transaction tagging."""

    __tablename__ = "category_rules"
    __tenant_scoped__ = True
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_category_rules_user_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    keywords: Mapped[str] = mapped_column(Text, default="", nullable=False)
    flow_type: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)
    monthly_budget_myr: Mapped[float | None] = mapped_column(Float, nullable=True)
    color: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    day_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    day_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_direction: Mapped[str] = mapped_column(String(16), default="any", nullable=False)
    account_filter: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class CategoryModelState(Base):
    """Persisted Naive Bayes category learning model trained on user edits."""

    __tablename__ = "category_model_state"
    __tenant_scoped__ = True

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False, default=get_current_user_id)
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    manual_sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    categories_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    model_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RecurringBill(Base):
    """User-defined monthly recurring bill or subscription tracked against ledger expenses."""

    __tablename__ = "recurring_bills"
    __tenant_scoped__ = True
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_recurring_bills_user_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=get_current_user_id)
    name: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="Subscriptions & Digital", nullable=False)
    amount_myr: Mapped[float] = mapped_column(Float, nullable=False)
    due_day: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    frequency: Mapped[str] = mapped_column(String(20), default="monthly", nullable=False)
    interval_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    match_keywords: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

