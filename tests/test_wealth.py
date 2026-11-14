from datetime import date, datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.modules.wealth import service
from app.modules.wealth.schemas import ManualLedgerRequest, SenderWhitelistItem


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()


def test_discover_whitelist_sync_and_idempotency(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "email_mode", "demo")
    db = _make_session()
    start = date(2026, 9, 1)
    end = date(2026, 11, 14)

    discovered = service.discover_senders(db, start, end)
    emails = {s.sender_email: s for s in discovered}
    assert emails["no-reply@bolt.eu"].is_money_related is True
    assert emails["noreply@touchngo.com.my"].is_money_related is True
    assert emails["noreply@wise.com"].is_money_related is True
    assert emails["noreply@rytbank.my"].is_money_related is True
    assert emails["promo@lazada.com.my"].is_money_related is False
    assert emails["jobs-noreply@linkedin.com"].is_money_related is False

    # User selects only Bolt, TNG, and Wise
    selected = [
        SenderWhitelistItem(sender_email="no-reply@bolt.eu", display_name="Bolt", category_hint="Transport"),
        SenderWhitelistItem(sender_email="noreply@touchngo.com.my", display_name="Touch 'n Go", category_hint="Parking & E-Wallet"),
        SenderWhitelistItem(sender_email="noreply@wise.com", display_name="Wise", category_hint="AI Freelance"),
    ]
    saved = service.save_whitelist(db, selected)
    assert len(saved) == 3

    # First sync: should pull Bolt, TNG, and Wise only (11 emails in demo seed)
    res1 = service.sync_whitelisted_transactions(db, start, end)
    assert res1.inserted_count == 11
    assert res1.skipped_duplicates == 0
    assert set(res1.whitelisted_senders_used) == {
        "no-reply@bolt.eu",
        "noreply@touchngo.com.my",
        "noreply@wise.com",
    }

    # Second sync over same range: idempotency must skip all 11
    res2 = service.sync_whitelisted_transactions(db, start, end)
    assert res2.inserted_count == 0
    assert res2.skipped_duplicates == 11

    summary = service.get_summary(db, start, end)
    # Wise payouts from clients/platforms default to repayment (not allowance income) unless tagged as an income rule
    assert summary.total_income_myr == 0.0
    assert summary.total_expense_myr == 113.70  # Bolt (18.4+22.9+15.6+19.8=76.7) + TNG (6+9+12+10=37.0)
    assert summary.net_cash_flow_myr == -113.70
    assert summary.rolling_30d_daily_burn_myr > 0
    assert len(summary.monthly_series) == 3
    assert summary.monthly_series[0].month == "2026-09"


def test_manual_freelance_entry():
    db = _make_session()
    entry = service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="Local AI Client",
            category="AI Freelance",
            tx_type="income",
            amount_myr=1500.0,
            occurred_at=datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc),
        ),
    )
    assert entry.amount_myr == 1500.0
    assert entry.tx_type == "income"


def test_skip_pidm_marketing_and_detect_income():
    from unittest.mock import patch
    from app.modules.wealth.email_client import RawEmail
    from app.modules.wealth.parser import parse_transaction_email

    promo = RawEmail(
        message_id="promo-pidm",
        sender_email="hello@marketing.gxbank.my",
        sender_name="GXBank",
        subject="Your savings are protected by PIDM up to RM 250,000",
        body="Enjoy peace of mind! Protected by PIDM up to RM 250,000 per depositor.",
        received_at=datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc),
    )
    assert parse_transaction_email(promo) is None

    incoming = RawEmail(
        message_id="ryt-in-1",
        sender_email="info@alerts.rytbank.my",
        sender_name="Ryt Bank",
        subject="DuitNow Transfer Received",
        body="You have received RM 450.00 via DuitNow into your Ryt Bank account.",
        received_at=datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc),
    )
    parsed = parse_transaction_email(incoming)
    assert parsed is not None
    assert parsed.tx_type == "repayment"
    assert parsed.amount_myr == 450.00


def test_tng_money_in_and_money_out_summary():
    from unittest.mock import patch
    from app.modules.wealth.email_client import RawEmail
    from app.modules.wealth.parser import parse_transaction_emails

    tng_summary = RawEmail(
        message_id="tng-sum-1",
        sender_email="noreply@tngdigital.com.my",
        sender_name="Touch 'n Go eWallet",
        subject="Here's your spending summary for Aug 2026",
        body="Money in RM12,071.59 Money out RM11,747.85 GO+ This month you've earned +RM1.99",
        received_at=datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc),
    )
    txs = parse_transaction_emails(tng_summary)
    assert len(txs) == 2
    by_type = {t.tx_type: t for t in txs}
    assert by_type["income"].amount_myr == 12071.59
    assert by_type["expense"].amount_myr == 11747.85


def test_grab_only_parses_e_receipts():
    from unittest.mock import patch
    from app.modules.wealth.email_client import RawEmail
    from app.modules.wealth.parser import parse_transaction_emails

    receipt = RawEmail(
        message_id="grab-rec-1",
        sender_email="no-reply@grab.com",
        sender_name="Grab",
        subject="Your Grab E-Receipt",
        body="Hope you enjoyed your food! TOTAL RM 22.35 DATE | TIME Pick-up time: 29 Oct 26",
        received_at=datetime(2026, 10, 29, 19, 0, tzinfo=timezone.utc),
    )
    promo = RawEmail(
        message_id="grab-promo-1",
        sender_email="no-reply@grab.com",
        sender_name="Grab Malaysia",
        subject="Your favourite restaurant has more to offer!",
        body="Grab Enjoy up to 25% off total bill when you eat out with Grab Dine Out RM 50 voucher!",
        received_at=datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc),
    )
    parsed_receipt = parse_transaction_emails(receipt)
    assert len(parsed_receipt) == 1
    assert parsed_receipt[0].amount_myr == 22.35
    assert parsed_receipt[0].tx_type == "expense"

    assert parse_transaction_emails(promo) == []


def test_csv_bank_statement_backfill_and_idempotency():
    db = _make_session()
    csv_data = (
        "Date,Description,Debit,Credit\n"
        "01/09/2026,Opening Balance,,\n"
        "02/09/2026,DuitNow QR Jaya Grocer,45.80,\n"
        "05/09/2026,AI Freelance Wise Payout,,3200.00\n"
        "10/09/2026,Setel Petronas,68.00,\n"
    ).encode("utf-8")

    res1 = service.backfill_from_statement(db, "ryt_sep.csv", csv_data, "Ryt Bank")
    assert res1.parsed_rows == 3
    assert res1.inserted_count == 3
    assert res1.skipped_duplicates == 0

    # Re-uploading same statement must be idempotent
    res2 = service.backfill_from_statement(db, "ryt_sep.csv", csv_data, "Ryt Bank")
    assert res2.inserted_count == 0
    assert res2.skipped_duplicates == 3

    summary = service.get_summary(db)
    assert summary.total_income_myr == 3200.00
    assert summary.total_expense_myr == 113.80


def test_locked_pdf_raises_and_unlocks():
    import io
    import pytest
    from pypdf import PdfWriter
    from app.modules.wealth.statement_parser import PdfLockedError, parse_pdf_statement

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret123")
    buf = io.BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()

    with pytest.raises(PdfLockedError):
        parse_pdf_statement(pdf_bytes, "Maybank")

    with pytest.raises(PdfLockedError):
        parse_pdf_statement(pdf_bytes, "Maybank", pdf_password="wrong")

    assert parse_pdf_statement(pdf_bytes, "Maybank", pdf_password="secret123") == []


def test_tng_skips_internal_go_plus_and_tracks_accounts_separately():
    from app.modules.settings import service as settings_service
    from app.modules.settings.schemas import UpdateSettingsRequest
    from app.modules.wealth.schemas import BankAccountUpsertRequest, ManualLedgerRequest
    from app.modules.wealth.statement_parser import _parse_tng_pdf_blocks

    lines = [
        "05/11/2026 Success eWallet Cash Out 20261105001 Via eWallet to GO+ RM1,122.62 RM100.00",
        "05/11/2026 Success GO+ Cash In 20261105002 Via eWallet to GO+ RM1,122.62 RM1,222.62",
        "05/11/2026 Success DUITNOW_RECEI 20261105003 VEFROM 5799 MIKHAIL RM14.19 RM114.19",
        "05/11/2026 Success DUITNOW_TRANS 20261105004 FERTO 0743 LIM SHIAW YONG RM1,000.00 RM50.00",
        "05/11/2026 Success Payment 20261105005 GOOGLE PLAY RM24.99 RM25.01",
    ]
    parsed = _parse_tng_pdf_blocks(lines, "Touch 'n Go")
    # Internal eWallet <-> GO+ pair must be skipped; only 3 real rows remain
    assert len(parsed) == 3
    assert parsed[0].tx_type == "repayment"
    assert parsed[0].amount_myr == 14.19
    assert parsed[1].tx_type == "expense"
    assert parsed[1].amount_myr == 1000.00
    assert parsed[2].tx_type == "expense"
    assert parsed[2].amount_myr == 24.99

    db = _make_session()
    settings_service.update_settings(
        db, UpdateSettingsRequest(account_holder_names="LIM SHIAW YONG")
    )
    service.upsert_bank_account(db, BankAccountUpsertRequest(name="Touch 'n Go", opening_balance_myr=2000.0))
    service.upsert_bank_account(db, BankAccountUpsertRequest(name="Ryt Bank", opening_balance_myr=500.0))

    # Self-transfer out of Touch 'n Go (RM 1000) and into Ryt Bank (RM 1000)
    service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="DUITNOW_TRANS FERTO 0743 LIM SHIAW YONG",
            category="Bank / Retail",
            tx_type="expense",
            amount_myr=1000.0,
            occurred_at=datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc),
            account_name="Touch 'n Go",
        ),
    )
    service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="DuitNow In from Shiaw Yong Lim",
            category="Bank / Retail",
            tx_type="income",
            amount_myr=1000.0,
            occurred_at=datetime(2026, 11, 5, 10, 1, tzinfo=timezone.utc),
            account_name="Ryt Bank",
        ),
    )
    # Real spending on Touch 'n Go
    service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="Google Play",
            category="General",
            tx_type="expense",
            amount_myr=24.99,
            occurred_at=datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc),
            account_name="Touch 'n Go",
        ),
    )

    summary = service.get_summary(db)
    # Self-transfers excluded from total income/expense
    assert summary.total_income_myr == 0.0
    assert summary.total_expense_myr == 24.99

    by_acct = {a.name: a for a in summary.account_balances}
    # Touch 'n Go: 2000 - 1000 - 24.99 = 975.01; Ryt Bank: 500 + 1000 = 1500.00
    assert by_acct["Touch 'n Go"].current_balance_myr == 975.01
    assert by_acct["Ryt Bank"].current_balance_myr == 1500.00

    # Setting current_balance_myr directly back-calculates opening_balance_myr
    service.upsert_bank_account(db, BankAccountUpsertRequest(name="Ryt Bank", current_balance_myr=1234.56))
    updated = {a.name: a for a in service.get_summary(db).account_balances}
    assert updated["Ryt Bank"].current_balance_myr == 1234.56
    assert updated["Ryt Bank"].opening_balance_myr == 234.56


def test_sync_single_bank_account_filter(monkeypatch):
    from app.config import settings as app_settings
    from app.modules.wealth.schemas import SenderWhitelistItem

    monkeypatch.setattr(app_settings, "email_mode", "demo")
    db = _make_session()
    service.save_whitelist(
        db,
        [
            SenderWhitelistItem(sender_email="noreply@touchngo.com.my", display_name="Touch 'n Go eWallet", category_hint="Parking & E-Wallet"),
            SenderWhitelistItem(sender_email="noreply@rytbank.my", display_name="Ryt Bank", category_hint="Bank / Retail"),
        ],
    )
    res = service.sync_whitelisted_transactions(
        db, date(2026, 11, 1), date(2026, 11, 15), account_name="Ryt Bank"
    )
    assert res.whitelisted_senders_used == ["noreply@rytbank.my"]
    assert res.inserted_count == 2
    entries = service.list_entries(db)
    assert all(e.account_name == "Ryt Bank" for e in entries)


def test_ryt_bank_email_with_pidm_footer_parses_and_detects_self_transfer():
    from unittest.mock import patch
    from app.modules.wealth.email_client import RawEmail
    from app.modules.wealth.parser import is_self_transfer_match, parse_transaction_email

    ryt = RawEmail(
        message_id="ryt-001",
        sender_email="info@alerts.rytbank.my",
        sender_name="Ryt Bank",
        subject="Your money is in!",
        body=(
            "Hi LIM SHIAW YONG,\n"
            "Money's in! You've received RM0.01 from LIM SHIAW YONG on 14/11/2026, 4:05 PM (GMT+8).\n"
            "Ryt Bank is a member of PIDM. Deposits are protected by PIDM up to RM250,000 for each depositor."
        ),
        received_at=datetime(2026, 11, 14, 16, 5, tzinfo=timezone.utc),
    )
    parsed = parse_transaction_email(ryt)
    assert parsed is not None
    assert parsed.tx_type == "repayment"
    assert parsed.amount_myr == 0.01
    assert parsed.merchant == "LIM SHIAW YONG"
    assert is_self_transfer_match(parsed.merchant, parsed.subject, "LIM SHIAW YONG") is True

    card_email = RawEmail(
        message_id="ryt-002",
        sender_email="info@alerts.rytbank.my",
        sender_name="Ryt Bank",
        subject="Card payment completed 👍",
        body="Hi LIM SHIAW YONG,\nRM2.30 was paid at 99 SPEEDMART-3122 using your Main Account on 11/11/2026, 6:08 PM (GMT+8).",
        received_at=datetime(2026, 11, 11, 18, 8, tzinfo=timezone.utc),
    )
    card_parsed = parse_transaction_email(card_email)
    assert card_parsed is not None
    assert card_parsed.tx_type == "expense"
    assert card_parsed.amount_myr == 2.30
    assert card_parsed.merchant == "99 SPEEDMART-3122"

    failed_email = RawEmail(
        message_id="ryt-003",
        sender_email="info@alerts.rytbank.my",
        sender_name="Ryt Bank",
        subject="Your scheduled transfer hit a bump today",
        body="We tried to process your scheduled transfer of RM200.00, but it didn't go through due to insufficient funds.",
        received_at=datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc),
    )
    assert parse_transaction_email(failed_email) is None


def test_close_time_duplicate_conflict_detection_and_resolution():
    from app.modules.wealth.schemas import ManualLedgerRequest, ResolveConflictRequest

    db = _make_session()
    e1 = service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="LOY GUANG MING",
            category="Bank / Retail",
            tx_type="expense",
            amount_myr=1865.95,
            occurred_at=datetime(2026, 11, 10, 12, 3, tzinfo=timezone.utc),
            account_name="Ryt Bank",
        ),
    )
    e2 = service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="LOY GUANG MING",
            category="Bank / Retail",
            tx_type="expense",
            amount_myr=1865.95,
            occurred_at=datetime(2026, 11, 10, 12, 5, tzinfo=timezone.utc),
            account_name="Ryt Bank",
        ),
    )
    groups = service.list_conflict_groups(db, window_minutes=5)
    assert len(groups) == 1
    assert len(groups[0].entries) == 2

    res = service.resolve_conflict_group(db, ResolveConflictRequest(keep_ids=[e1.id], delete_ids=[e2.id]))
    assert res == {"kept": 1, "deleted": 1}
    assert service.list_conflict_groups(db, window_minutes=5) == []
    assert len(service.list_entries(db)) == 1


def test_categorize_claimable_and_delete_with_remark():
    from app.modules.wealth.schemas import ManualLedgerRequest, UpdateEntryRequest

    db = _make_session()
    e1 = service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="Grab Ride to Client",
            category="Transport",
            tx_type="expense",
            amount_myr=24.50,
            occurred_at=datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc),
            account_name="Ryt Bank",
        ),
    )
    updated = service.update_entry(db, e1.id, UpdateEntryRequest(category="Business Claim", is_self_transfer=True))
    assert updated is not None
    assert updated.category == "Business Claim"
    assert updated.is_claimable is True
    assert updated.is_self_transfer is True
    updated = service.update_entry(db, e1.id, UpdateEntryRequest(is_self_transfer=False))
    assert updated is not None
    assert updated.is_self_transfer is False

    assert service.delete_entry(db, e1.id, remark="Duplicate of Grab receipt") is True
    logs = service.list_deletion_logs(db)
    assert len(logs) == 1
    assert logs[0].merchant == "Grab Ride to Client"
    assert logs[0].remark == "Duplicate of Grab receipt"

    restored = service.restore_deleted_entry(db, logs[0].id)
    assert restored is not None
    assert restored.merchant == "Grab Ride to Client"
    assert restored.category == "Business Claim"
    assert restored.is_claimable is True
    assert restored.amount_myr == 24.50
    assert restored.account_name == "Ryt Bank"
    assert service.list_deletion_logs(db) == []
    assert len(service.list_entries(db)) == 1

    # Also verify restoring a legacy DeletionLog row with empty snapshot_json
    from app.modules.wealth.models import DeletionLog
    legacy_log = DeletionLog(
        original_entry_id=999,
        merchant="Legacy Merchant",
        category="General",
        account_name="Touch 'n Go",
        tx_type="expense",
        amount_myr=12.34,
        occurred_at=datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc),
        remark="Old deletion",
        snapshot_json={},
    )
    db.add(legacy_log)
    db.commit()
    db.refresh(legacy_log)
    restored_legacy = service.restore_deleted_entry(db, legacy_log.id)
    assert restored_legacy is not None
    assert restored_legacy.merchant == "Legacy Merchant"
    assert restored_legacy.amount_myr == 12.34


def test_live_listener_broadcast_and_settings_toggle():
    from app.modules.settings import service as settings_service
    from app.modules.settings.schemas import UpdateSettingsRequest
    from app.modules.wealth.listener import WealthLiveListener

    db = _make_session()
    cfg = settings_service.get_settings(db)
    assert cfg.live_listener_enabled is True
    updated = settings_service.update_settings(db, UpdateSettingsRequest(live_listener_enabled=False))
    assert updated.live_listener_enabled is False

    listener = WealthLiveListener()
    q = listener.subscribe()
    listener.broadcast({"type": "ledger_updated", "status": "OK: +1 new"})
    assert not q.empty()
    assert "ledger_updated" in q.get_nowait()
    listener.unsubscribe(q)


def test_grab_receipt_deduplicated_against_ryt_bank_card_payment():
    from app.modules.wealth.schemas import ManualLedgerRequest

    db = _make_session()
    service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="Grab",
            category="Transport",
            tx_type="expense",
            amount_myr=19.75,
            occurred_at=datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc),
            account_name="Unassigned",
        ),
    )
    service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="Grab* A-AXD4OJJGXA8QAV",
            category="Transport",
            tx_type="expense",
            amount_myr=19.75,
            occurred_at=datetime(2026, 11, 1, 10, 1, tzinfo=timezone.utc),
            account_name="Ryt Bank",
        ),
    )

    res = service.reconcile_ledger_entries(db)
    assert res["removed"] == 1
    remaining = service.list_entries(db)
    assert len(remaining) == 1
    assert remaining[0].account_name == "Ryt Bank"
    assert remaining[0].merchant == "Grab* A-AXD4OJJGXA8QAV"


def test_ryt_card_merchant_with_at_sign_and_refund():
    from app.modules.wealth.parser import extract_merchant_from_text

    assert (
        extract_merchant_from_text(
            "Card payment completed 👍",
            "Hi LIM SHIAW YONG, RM99.85 was paid at TFM-WCITY@OUG using your Main Account on 31/10/2026, 8:39 PM (GMT+8). If this was not you, give us a call at +603 5115 5115, or drop us a note at support@rytbank.my , or reach out via the app.",
            "Ryt Bank",
            False,
        )
        == "TFM-WCITY@OUG"
    )
    assert (
        extract_merchant_from_text(
            "Card payment completed 👍",
            "Hi LIM SHIAW YONG, RM115.50 was paid at MOJO @ DORAISAMY using your Main Account on 4/11/2026, 12:45 PM (GMT+8). If this was not you, give us a call at +603 5115 5115, or drop us a note at support@rytbank.my.",
            "Ryt Bank",
            False,
        )
        == "MOJO @ DORAISAMY"
    )
    assert (
        extract_merchant_from_text(
            "Card refund",
            "Hi LIM SHIAW YONG, RM4.00 from JUSTGO-EC has been refunded to your Main Account on 8/10/2026.",
            "Ryt Bank",
            True,
        )
        == "JUSTGO-EC"
    )


def test_sync_skips_resolved_duplicates_and_preserves_manual_edits():
    from unittest.mock import patch
    from app.modules.wealth.email_client import RawEmail
    from app.modules.wealth.schemas import ResolveConflictRequest, SenderWhitelistItem, UpdateEntryRequest

    db = _make_session()
    service.seed_default_category_rules(db)
    service.add_whitelist_sender(
        db, SenderWhitelistItem(sender_email="info@alerts.rytbank.my", display_name="Ryt Bank", category_hint="Bank / Retail")
    )

    class _FakeProvider:
        def __init__(self, emails):
            self._emails = emails

        def fetch_messages(self, start_date, end_date, sender_emails=None, headers_only=False):
            return self._emails

    provider = _FakeProvider(
        [
            RawEmail(
                message_id="ryt-dup-1",
                sender_email="info@alerts.rytbank.my",
                sender_name="Ryt Bank",
                subject="Card payment completed",
                body="Hi LIM SHIAW YONG, RM10.00 was paid at GRAB* RIDE using your Main Account on 10/11/2026.",
                received_at=datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc),
            ),
            RawEmail(
                message_id="ryt-dup-2",
                sender_email="info@alerts.rytbank.my",
                sender_name="Ryt Bank",
                subject="Card payment completed",
                body="Hi LIM SHIAW YONG, RM10.00 was paid at GRAB* RIDE using your Main Account on 10/11/2026.",
                received_at=datetime(2026, 11, 10, 12, 2, tzinfo=timezone.utc),
            ),
        ]
    )

    with patch("app.modules.wealth.service.get_email_provider", return_value=provider):
        res1 = service.sync_whitelisted_transactions(db, date(2026, 11, 1), date(2026, 11, 15))
        assert res1.inserted_count == 2

        groups = service.list_conflict_groups(db, window_minutes=5)
        assert len(groups) == 1
        kept_id = groups[0].entries[0].id
        del_id = groups[0].entries[1].id

        service.resolve_conflict_group(db, ResolveConflictRequest(keep_ids=[kept_id], delete_ids=[del_id]))
        service.update_entry(db, kept_id, UpdateEntryRequest(category="Food & Beverage", is_self_transfer=True))

        # Re-syncing the same window must NOT bring back the resolved duplicate or overwrite manual edits
        res2 = service.sync_whitelisted_transactions(db, date(2026, 11, 1), date(2026, 11, 15))
        assert res2.inserted_count == 0
        entries = service.list_entries(db)
        assert len(entries) == 1
        assert entries[0].id == kept_id
        assert entries[0].category == "Food & Beverage"
        assert entries[0].is_self_transfer is True


def test_top_transfer_counterparty_rankings():
    from app.modules.wealth.schemas import ManualLedgerRequest

    db = _make_session()
    # Outgoing transfers (two to Loy Guang Ming across TNG + Ryt, one to Alice)
    service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="DUITNOW_TRANS FERTO 1981 LOY GUANG MING",
            category="P2P Transfers",
            tx_type="expense",
            amount_myr=100.0,
            occurred_at=datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc),
            account_name="Touch 'n Go",
        ),
    )
    service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="LOY GUANG MING",
            category="Bank / Retail",
            tx_type="expense",
            amount_myr=250.0,
            occurred_at=datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc),
            account_name="Ryt Bank",
            notes="Nice! Transfer settled!",
        ),
    )
    service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="Transfer to Wallet 1234 ALICE TAN",
            category="P2P Transfers",
            tx_type="expense",
            amount_myr=50.0,
            occurred_at=datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc),
            account_name="Touch 'n Go",
        ),
    )
    # Incoming transfers from people
    service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="DUITNOW_RECEI VEFROM 9584 BOB LEE",
            category="P2P Transfers",
            tx_type="income",
            amount_myr=400.0,
            occurred_at=datetime(2026, 11, 11, 11, 0, tzinfo=timezone.utc),
            account_name="Touch 'n Go",
        ),
    )
    service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="ALICE TAN",
            category="Bank / Retail",
            tx_type="income",
            amount_myr=75.0,
            occurred_at=datetime(2026, 11, 12, 14, 0, tzinfo=timezone.utc),
            account_name="Ryt Bank",
        ),
    )

    summary = service.get_summary(db)
    assert len(summary.top_transfers_out) == 2
    assert summary.top_transfers_out[0].name == "LOY GUANG MING"
    assert summary.top_transfers_out[0].total_myr == 350.0
    assert summary.top_transfers_out[0].tx_count == 2
    assert summary.top_transfers_out[1].name == "ALICE TAN"
    assert summary.top_transfers_out[1].total_myr == 50.0

    assert len(summary.top_transfers_in) == 2
    assert summary.top_transfers_in[0].name == "BOB LEE"
    assert summary.top_transfers_in[0].total_myr == 400.0
    assert summary.top_transfers_in[1].name == "ALICE TAN"
    assert summary.top_transfers_in[1].total_myr == 75.0


def test_smart_category_learning_model_trains_and_predicts():
    from app.modules.wealth import category_model
    from app.modules.wealth.schemas import ManualLedgerRequest, UpdateEntryRequest

    db = _make_session()
    e1 = service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="MIX - SRI PETALING, KL",
            category="Bank / Retail",
            tx_type="expense",
            amount_myr=4.90,
            occurred_at=datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc),
            account_name="Ryt Bank",
        ),
    )
    e2 = service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="MIX - BUKIT JALIL",
            category="Bank / Retail",
            tx_type="expense",
            amount_myr=6.50,
            occurred_at=datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc),
            account_name="Ryt Bank",
        ),
    )
    service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="SHELL STATION PJ",
            category="Fuel & Toll",
            tx_type="expense",
            amount_myr=50.0,
            occurred_at=datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc),
            account_name="Ryt Bank",
        ),
    )

    # User manually categorizes MIX - SRI PETALING as Food & Beverage -> triggers auto-train
    service.update_entry(db, e1.id, UpdateEntryRequest(category="Food & Beverage"))
    state = category_model.get_model_state(db)
    assert state is not None
    assert state.manual_sample_count == 1
    assert state.sample_count >= 2

    # Model should now suggest Food & Beverage for unedited MIX - BUKIT JALIL
    sugs = category_model.preview_suggestions(db)
    assert any(s["entry_id"] == e2.id and s["suggested_category"] == "Food & Beverage" for s in sugs)

    applied = category_model.apply_suggestions(db)
    assert applied >= 1
    db.refresh(e2)
    assert e2.category == "Food & Beverage"


def test_cimb_four_digit_non_comma_amount_and_reconcile():
    from app.modules.wealth.email_client import RawEmail
    from app.modules.wealth.models import LedgerEntry
    from app.modules.wealth.parser import parse_transaction_email

    body = (
        "Dear Valued Customer, "
        "You've received RM1122.58 in account ending ******4984 on 04NOV2026 15:16:38. "
        "More transaction details are available on the CIMB OCTO App or CIMB Clicks website. "
        "Download CIMB OCTO App now from the official app stores or click here to learn more."
    )
    raw = RawEmail(
        message_id="cimb-1122-58",
        sender_email="alerts@cimb.com",
        sender_name="CIMB",
        subject="Transaction Notification",
        body=body,
        received_at=datetime(2026, 11, 4, 15, 16, tzinfo=timezone.utc),
    )
    parsed = parse_transaction_email(raw)
    assert parsed is not None
    assert parsed.amount_myr == 1122.58
    assert parsed.tx_type == "repayment"
    assert parsed.merchant == "CIMB"

    # Also verify reconcile_ledger_entries repairs previously truncated 112.00 rows
    db = _make_session()
    broken = LedgerEntry(
        idempotency_hash="legacy-cimb-112",
        sender_email="alerts@cimb.com",
        merchant="the official app stores or click here to learn ...",
        category="Bank / Retail",
        tx_type="income",
        amount_myr=112.00,
        occurred_at=datetime(2026, 11, 4, 15, 16, tzinfo=timezone.utc),
        subject="Transaction Notification",
        account_name="CIMB",
        raw_payload={"message_id": "cimb-1122-58", "sender_name": "CIMB", "snippet": body[:300]},
    )
    db.add(broken)
    db.commit()

    service.reconcile_ledger_entries(db)
    db.refresh(broken)
    assert broken.amount_myr == 1122.58
    assert broken.merchant == "CIMB"


def test_tng_transfer_to_wallet_is_expense_and_receive_from_wallet_is_income():
    from app.modules.wealth.models import LedgerEntry
    from app.modules.wealth.statement_parser import _parse_tng_pdf_blocks

    lines = [
        "13/11/2026 Success Transfer to Wallet 8339 Fund Transfer RM19.25 RM316.37",
        "13/11/2026 Success Receive from Wallet 3995 CHIN YEW SAN RM66.80 RM335.62",
    ]
    txs = _parse_tng_pdf_blocks(lines, "Touch 'n Go")
    assert len(txs) == 2
    assert txs[0].tx_type == "expense"
    assert txs[0].amount_myr == 19.25
    assert txs[1].tx_type == "repayment"
    assert txs[1].amount_myr == 66.80

    # Also verify reconcile_ledger_entries repairs legacy misclassified TNG rows
    db = _make_session()
    wrong_out = LedgerEntry(
        idempotency_hash="legacy-tng-out",
        sender_email="statement@touch'ngo",
        merchant="Transfer to Wallet 8339 Fund Transfer",
        category="P2P Transfers",
        tx_type="income",
        amount_myr=19.25,
        occurred_at=datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc),
        subject="[Touch 'n Go Statement] Transfer to Wallet: 8339 Fund Transfer",
        account_name="Touch 'n Go",
    )
    wrong_in = LedgerEntry(
        idempotency_hash="legacy-tng-in",
        sender_email="statement@touch'ngo",
        merchant="Receive from Wallet 3995 CHIN YEW SAN",
        category="P2P Transfers",
        tx_type="expense",
        amount_myr=66.80,
        occurred_at=datetime(2026, 11, 13, 11, 0, tzinfo=timezone.utc),
        subject="[Touch 'n Go Statement] Receive from Wallet: 3995 CHIN YEW SAN",
        account_name="Touch 'n Go",
    )
    db.add_all([wrong_out, wrong_in])
    db.commit()

    service.reconcile_ledger_entries(db)
    db.refresh(wrong_out)
    db.refresh(wrong_in)
    assert wrong_out.tx_type == "expense"
    assert wrong_in.tx_type == "repayment"


def test_custom_category_rule_flow_type_income_repayment_expense():
    from app.modules.wealth.schemas import CategoryRuleUpsertRequest, ManualLedgerRequest

    db = _make_session()
    service.seed_default_category_rules(db)

    # Custom Allowance rule -> income; Friend Repay -> repayment
    service.upsert_category_rule(
        db, CategoryRuleUpsertRequest(name="Allowance", keywords="monthly allowance, dad", flow_type="income")
    )
    service.upsert_category_rule(
        db, CategoryRuleUpsertRequest(name="Friend Repay", keywords="split bill, payback", flow_type="repayment")
    )

    e_allow = service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="Monthly Allowance from Dad",
            category="General",
            tx_type="repayment",
            amount_myr=1000.0,
            occurred_at=datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc),
            account_name="CIMB",
        ),
    )
    e_repay = service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="Alice Split Bill",
            category="General",
            tx_type="income",
            amount_myr=80.0,
            occurred_at=datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc),
            account_name="CIMB",
        ),
    )
    service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="Lunch Cafe",
            category="Food & Beverage",
            tx_type="expense",
            amount_myr=30.0,
            occurred_at=datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc),
            account_name="CIMB",
        ),
    )

    service.reconcile_ledger_entries(db)
    db.refresh(e_allow)
    db.refresh(e_repay)
    assert e_allow.category == "Allowance"
    assert e_allow.tx_type == "income"
    assert e_repay.category == "Friend Repay"
    assert e_repay.tx_type == "repayment"

    # Income (Selected) only includes Allowance (1000), NOT repayment (80)
    summary = service.get_summary(db, month="2026-11")
    assert summary.total_income_myr == 1000.0
    assert summary.total_repayment_myr == 80.0
    assert summary.total_expense_myr == 30.0
    assert any(d.date == "2026-11-02" and d.repayment_myr == 80.0 for d in summary.daily_series)

    # Bank balance includes BOTH allowance (+1000) and repayment (+80) minus expense (-30) = 1050
    accts = {a.name: a for a in service.list_bank_accounts(db)}
    assert accts["CIMB"].total_in_myr == 1080.0
    assert accts["CIMB"].current_balance_myr == 1050.0


def test_payment_linking_reduces_net_expense_and_keeps_account_balances():
    from app.modules.wealth.schemas import ManualLedgerRequest

    db = _make_session()
    mojo = service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="MOJO @ DORAISAMY",
            category="Food & Beverage",
            tx_type="expense",
            amount_myr=115.50,
            occurred_at=datetime(2026, 11, 4, 20, 0, tzinfo=timezone.utc),
            account_name="Ryt Bank",
        ),
    )
    rep_ids = []
    for idx, name in enumerate(("MIKHAEL", "HANSEN", "KWEK", "NG P")):
        r = service.add_manual_entry(
            db,
            ManualLedgerRequest(
                merchant=f"Receive from Wallet {name}",
                category="P2P Transfer In",
                tx_type="repayment",
                amount_myr=19.25,
                occurred_at=datetime(2026, 11, 4, 21, idx, tzinfo=timezone.utc),
                account_name="Touch 'n Go",
            ),
        )
        rep_ids.append(r.id)

    linked = service.link_repayments(db, mojo.id, rep_ids)
    assert linked is not None
    assert linked.linked_repaid_myr == 77.00
    assert linked.net_amount_myr == 38.50

    summary = service.get_summary(db, month="2026-11")
    # Net expense is 115.50 - (4 * 19.25) = 38.50
    assert summary.total_expense_myr == 38.50
    assert summary.total_repayment_myr == 77.00
    assert summary.category_breakdown["Food & Beverage"] == 38.50

    # Bank balances still track raw cash flow per account
    accts = {a.name: a for a in service.list_bank_accounts(db)}
    assert accts["Ryt Bank"].current_balance_myr == -115.50
    assert accts["Touch 'n Go"].current_balance_myr == 77.00

    # Unlinking one repayment updates net expense to 57.75
    service.unlink_repayment(db, rep_ids[0])
    summary2 = service.get_summary(db, month="2026-11")
    assert summary2.total_expense_myr == 57.75


def test_gmail_thread_id_parsing_and_direct_email_url():
    from app.modules.wealth.email_client import _parse_gm_thrid
    from app.modules.wealth.models import LedgerEntry

    thrid_hex = _parse_gm_thrid(b"42 (X-GM-THRID 1812345678901234567 RFC822 {1024}")
    assert thrid_hex == format(1812345678901234567, "x")

    db = _make_session()
    with_thread = LedgerEntry(
        idempotency_hash="thrid-1",
        sender_email="info@alerts.rytbank.my",
        merchant="PANAS EXPRESS SHERATON",
        category="Food & Beverage",
        tx_type="expense",
        amount_myr=3.50,
        occurred_at=datetime(2026, 11, 10, 7, 10, tzinfo=timezone.utc),
        subject="Card payment completed 👍",
        raw_payload={"message_id": "<abc@rytbank.my>", "gmail_thread_id": thrid_hex},
    )
    without_thread = LedgerEntry(
        idempotency_hash="thrid-2",
        sender_email="info@alerts.rytbank.my",
        merchant="MIX - SRI PETALING",
        category="Food & Beverage",
        tx_type="expense",
        amount_myr=4.90,
        occurred_at=datetime(2026, 11, 8, 14, 35, tzinfo=timezone.utc),
        subject="Card payment completed 👍",
        raw_payload={"message_id": "<def@rytbank.my>"},
    )
    db.add_all([with_thread, without_thread])
    db.commit()
    db.refresh(with_thread)
    db.refresh(without_thread)

    entries = service.list_entries(db)
    by_id = {e.id: e for e in entries}
    assert by_id[with_thread.id].gmail_thread_id == thrid_hex
    assert by_id[without_thread.id].gmail_thread_id is None

    assert service.resolve_entry_email_url(db, with_thread.id) == f"https://mail.google.com/mail/u/0/#all/{thrid_hex}"
    fallback_url = service.resolve_entry_email_url(db, without_thread.id)
    assert fallback_url is not None and "rfc822msgid" in fallback_url


def test_smart_repayment_suggestion_split_owed_recurring_subbudget_and_csv():
    from app.modules.wealth.parser import normalize_merchant_display
    from app.modules.wealth.schemas import (
        CategoryRuleUpsertRequest,
        ManualLedgerRequest,
        RecurringBillUpsertRequest,
    )

    assert normalize_merchant_display("DuitNow QR TNGOW3MY1 059 MYCHA MY@Quill City Mall 81675") == "MYCHA MY@Quill City Mall"
    assert normalize_merchant_display("DUITNOW_TRANS FERTO 0118 LIM SHIAW YONG") == "LIM SHIAW YONG"
    assert normalize_merchant_display("Grab* A-A2UIAIGWWBIAAV") == "Grab"
    assert normalize_merchant_display("GO+ Daily EarningsMMF2026110 0 Daily Interest") == "GO+ Daily Earnings"
    assert normalize_merchant_display("Receive from Wallet20261102111 1771 KWEK CHEE LING") == "KWEK CHEE LING"
    assert normalize_merchant_display("Payment TNGOW3MY1 941 711") == "711"
    assert normalize_merchant_display("MICROSOFT*PC GAME PASS 1") == "MICROSOFT PC GAME PASS 1"

    db = _make_session()
    service.upsert_category_rule(
        db,
        CategoryRuleUpsertRequest(
            name="Food & Beverage",
            keywords="mycha, grabfood",
            flow_type="expense",
            monthly_budget_myr=500.0,
        ),
    )

    # Manual recurring bills: one paid in 2026-11 (Spotify), one unpaid (Unifi)
    spotify_bill = service.upsert_recurring_bill(
        db,
        RecurringBillUpsertRequest(
            name="Spotify",
            amount_myr=17.50,
            due_day=5,
            match_keywords="spotify",
        ),
    )
    unifi_bill = service.upsert_recurring_bill(
        db,
        RecurringBillUpsertRequest(
            name="Unifi Fibre",
            amount_myr=139.00,
            due_day=15,
            match_keywords="unifi",
        ),
    )
    service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="Spotify",
            category="Subscriptions & Digital",
            tx_type="expense",
            amount_myr=17.50,
            occurred_at=datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc),
            account_name="Ryt Bank",
        ),
    )

    # Group dinner split 4 ways (RM 120.00 -> each owes RM 30.00, expected repaid RM 90.00)
    dinner = service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="DuitNow QR TNGOW3MY1 059 MYCHA DINNER 81675",
            category="Food & Beverage",
            tx_type="expense",
            amount_myr=120.00,
            occurred_at=datetime(2026, 11, 10, 19, 0, tzinfo=timezone.utc),
            account_name="Touch 'n Go",
        ),
    )
    rep = service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="Receive from Wallet 5190 MIKHAEL",
            category="P2P Transfer In",
            tx_type="repayment",
            amount_myr=30.00,
            occurred_at=datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc),
            account_name="Touch 'n Go",
        ),
    )

    entries = {e.id: e for e in service.list_entries(db)}
    assert entries[dinner.id].display_merchant == "MYCHA DINNER"
    assert entries[rep.id].display_merchant == "MIKHAEL"
    assert entries[rep.id].suggested_link_entry_id == dinner.id

    # Accept suggestion & set split_count=4 -> RM 60.00 still owed
    accepted = service.accept_suggested_repayment_link(db, rep.id)
    assert accepted is not None and accepted.linked_entry_id == dinner.id
    updated_dinner = service.link_repayments(db, dinner.id, [rep.id], split_count=4)
    assert updated_dinner is not None
    assert updated_dinner.owed_remaining_myr == 60.00

    summary = service.get_summary(db, month="2026-11")
    assert summary.category_budgets.get("Food & Beverage") == 500.0
    bills_by_id = {b.id: b for b in summary.recurring_bills}
    assert bills_by_id[spotify_bill.id].paid_this_month is True
    assert bills_by_id[unifi_bill.id].paid_this_month is False
    assert summary.remaining_fixed_bills_myr == 139.00

    assert service.delete_recurring_bill(db, unifi_bill.id) is True

    # Manual entry with custom_days recurrence creates both ledger entry and recurring bill
    service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="Car Wash",
            category="Transport",
            tx_type="expense",
            amount_myr=25.00,
            occurred_at=datetime(2026, 11, 12, 11, 0, tzinfo=timezone.utc),
            account_name="Touch 'n Go",
            recurrence_mode="custom_days",
            interval_days=14,
        ),
    )
    bills_after = {b.name: b for b in service.list_recurring_bills(db, month="2026-11")}
    assert "Car Wash" in bills_after
    assert bills_after["Car Wash"].frequency == "custom_days"
    assert bills_after["Car Wash"].interval_days == 14
    assert bills_after["Car Wash"].paid_this_month is True

    csv_text = service.export_entries_csv(db, month="2026-11")
    assert "MYCHA DINNER" in csv_text
    assert "90.00" in csv_text  # Net after RM 30 repayment




