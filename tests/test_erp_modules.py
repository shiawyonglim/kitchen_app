from datetime import date, datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.modules.assistant import service as assistant_service
from app.modules.assistant.schemas import AssistantIngestRequest
from app.modules.friction import service as friction_service
from app.modules.friction.schemas import CreateDebtRequest, LogOdometerRequest, UpdateDebtRequest
from app.modules.kitchen import service as kitchen_service
from app.modules.kitchen import taste as kitchen_taste
from app.modules.kitchen.schemas import (
    AddKitchenItemRequest,
    CookedMealRequest,
    ShoppingListCreate,
    TasteFeedbackRequest,
    TasteProfileUpsert,
    TasteRuleCreate,
)
from app.modules.trainer import service as trainer_service
from app.modules.trainer.schemas import LogWorkoutRequest
from app.modules.wealth import service as wealth_service
from app.modules.wealth.schemas import ManualLedgerRequest


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()


def test_trainer_updates_kitchen_dinner_recovery():
    db = _make_session()
    kitchen_service.add_item(
        db,
        AddKitchenItemRequest(
            name="Fresh Kangkung",
            category="Produce",
            quantity=1,
            unit="bunch",
            shelf_life_days=6,
            days_old=5,
        ),
    )

    items = kitchen_service.list_items(db)
    kangkung = next(i for i in items if "Kangkung" in i.name)
    assert kangkung.is_expiring_soon is True

    # Log a sub-30 minute 5K run -> must boost Kitchen recovery macros
    res = trainer_service.log_workout(
        db,
        LogWorkoutRequest(
            exercise_type="5K Run",
            distance_km=5.0,
            duration_min=28.5,
            sensor_source="GPS Telemetry",
        ),
    )
    assert res.kitchen_target.recovery_mode is True
    assert res.kitchen_target.bonus_protein_g == 36
    assert "+2 Kampung Eggs (12g protein)" in res.kitchen_target.recommended_additions

    recipe = kitchen_service.synthesize_dinner_recipe(db)
    assert "Fresh Kangkung" in recipe.used_expiring_items
    assert len(recipe.recovery_additions) == 2


def test_friction_altis_cost_gear_and_social_debt():
    db = _make_session()
    friction_service.seed_default_gear(db)

    # Log Setel fuel expense in Wealth Ledger + 2 odometer readings
    wealth_service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="Setel (Petronas)",
            category="Fuel",
            tx_type="expense",
            amount_myr=100.0,
            occurred_at=datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc),
        ),
    )
    friction_service.log_odometer(db, LogOdometerRequest(odometer_km=80000))
    friction_service.log_odometer(db, LogOdometerRequest(odometer_km=80500))

    # Parse TNG split bill for San Guo Sha night
    debts = friction_service.parse_tng_split_bill(
        db, "San Guo Sha Night", "Alex RM 25.00\nJason RM 30.00"
    )
    assert len(debts) == 2

    summary = friction_service.get_summary(db)
    assert summary.altis.distance_km == 500.0
    assert summary.altis.cost_per_km_myr == 0.2
    assert summary.total_owed_myr == 55.0

    # Settle one debt
    friction_service.settle_debt(db, debts[0].id)
    summary2 = friction_service.get_summary(db)
    assert summary2.total_owed_myr == 30.0


def test_local_onnx_receipt_ocr():
    import io
    from PIL import Image, ImageDraw
    from app.modules.kitchen import ocr

    img = Image.new("RGB", (500, 220), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((30, 30), "FRESH KANGKUNG", fill="black")
    draw.text((30, 80), "CHICKEN BREAST", fill="black")
    draw.text((30, 130), "TOTAL RM 18.90", fill="black")

    buf = io.BytesIO()
    img.save(buf, format="PNG")

    lines = ocr.extract_receipt_lines(buf.getvalue())
    joined = " ".join(lines).lower()
    assert "kangkung" in joined or "chicken" in joined
    assert not any("total" in line.lower() for line in lines)


def test_kitchen_adjust_quantity_and_wealth_toggle():
    db = _make_session()
    kitchen_service.add_item(
        db,
        AddKitchenItemRequest(name="Kampung Eggs", category="Protein", quantity=10, unit="pcs"),
    )
    eggs = next(i for i in kitchen_service.list_items(db) if "Eggs" in i.name)
    assert eggs.quantity == 10.0

    updated = kitchen_service.adjust_item_quantity(db, eggs.id, -2)
    assert updated is not None
    assert updated.quantity == 8.0

    entry = wealth_service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="Test Transfer",
            category="Bank / Retail",
            tx_type="expense",
            amount_myr=50.0,
            occurred_at=datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc),
        ),
    )
    flipped = wealth_service.toggle_entry_type(db, entry.id)
    assert flipped is not None
    assert flipped.tx_type == "income"
    assert wealth_service.delete_entry(db, entry.id) is True


def test_fastapi_endpoints_smoke(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import app.database as app_db
    import app.main as app_main
    from app.config import settings as app_settings

    test_db_path = tmp_path / "test_smoke.db"
    test_engine = create_engine(
        f"sqlite:///{test_db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def _override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(app_settings, "email_mode", "demo")
    monkeypatch.setattr(app_db, "engine", test_engine)
    monkeypatch.setattr(app_db, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(app_main, "SessionLocal", TestSessionLocal)
    app_main.app.dependency_overrides[app_db.get_db] = _override_get_db

    try:
        with TestClient(app_main.app) as client:
            assert client.get("/api/health").status_code == 200
            # Unauthenticated API requests must be rejected
            assert client.get("/api/wealth/summary").status_code == 401
            assert client.get("/", follow_redirects=False).status_code == 303

            # Bootstrap owner account
            csrf_res = client.get("/api/auth/csrf")
            assert csrf_res.status_code == 200
            pre_csrf = csrf_res.json()["csrf_token"]
            reg = client.post(
                "/api/auth/register",
                json={
                    "full_name": "Smoke Owner",
                    "email": "owner@example.com",
                    "password": "Strong!Password#2026",
                },
                headers={"X-CSRF-Token": pre_csrf},
            )
            assert reg.status_code == 201
            csrf_token = client.cookies.get("erp_csrf", "")
            assert csrf_token

            assert client.get("/").status_code == 200
            assert client.get("/api/wealth/summary").status_code == 200
            assert client.get("/api/wealth/summary?month=2026-11").status_code == 200
            assert client.get("/api/wealth/entries?month=2026-11").status_code == 200
            assert client.get("/api/assets").status_code == 200
            assert client.get("/api/kitchen/items").status_code == 200
            assert client.get("/api/trainer/workouts").status_code == 200
            assert client.get("/api/friction/summary").status_code == 200

            settings_resp = client.get("/api/settings")
            assert settings_resp.status_code == 200
            current = settings_resp.json()
            assert "auto_sync_enabled" in current

            put_resp = client.put(
                "/api/settings",
                json={
                    "auto_sync_enabled": True,
                    "auto_sync_hour": 8,
                    "auto_sync_lookback_days": 7,
                    "monthly_budget_myr": 3500.0,
                    "default_calories": 2200,
                    "default_protein_g": 150,
                },
                headers={"X-CSRF-Token": csrf_token},
            )
            assert put_resp.status_code == 200
            updated = put_resp.json()
            assert updated["auto_sync_hour"] == 8
            assert updated["monthly_budget_myr"] == 3500.0

            sync_resp = client.post(
                "/api/settings/run-auto-sync",
                headers={"X-CSRF-Token": csrf_token},
            )
            assert sync_resp.status_code == 200
            synced = sync_resp.json()
            assert synced["last_auto_sync_at"] is not None
            assert synced["last_auto_sync_status"].startswith("OK:")

            assert client.get("/api/kitchen/taste/analysis").status_code == 200
            assert client.get("/api/kitchen/taste/rules").status_code == 200
            assert client.get("/api/kitchen/taste/profiles").status_code == 200
            fb_resp = client.post(
                "/api/kitchen/taste/feedback",
                json={
                    "dish_title": "Smoke Dish",
                    "ingredients_used": ["Tomato", "Chili Padi"],
                    "applied_rule_ids": [],
                    "verdict": "loved",
                },
                headers={"X-CSRF-Token": csrf_token},
            )
            assert fb_resp.status_code == 200
            assert fb_resp.json()["palate"]["feedback_count"] == 1

            # Assistant authenticated endpoints + WhatsApp bridge token check
            assert client.get("/api/assistant/items").status_code == 200
            assert client.get("/api/assistant/morning-briefing").status_code == 200
            assert client.get("/api/assistant/whatsapp-status").status_code == 200
            assert client.post(
                "/api/assistant/whatsapp-ingest",
                json={"raw_message": "Meet Jason tomorrow 7pm", "source": "whatsapp"},
            ).status_code == 401
            monkeypatch.setattr(app_settings, "whatsapp_bridge_token", "test-bridge-secret")
            wa_ok = client.post(
                "/api/assistant/whatsapp-ingest",
                json={"raw_message": "Meet Jason tomorrow 7pm", "source": "whatsapp", "sender": "Me"},
                headers={"X-Bridge-Token": "test-bridge-secret"},
            )
            assert wa_ok.status_code == 200
            assert wa_ok.json()[0]["category"] == "schedule"
    finally:
        app_main.app.dependency_overrides.clear()
        test_engine.dispose()


def test_kitchen_taste_knowledgebase_and_learning():
    db = _make_session()
    kitchen_taste.upsert_profile(
        db,
        TasteProfileUpsert(name="Chili Padi", spicy=0.95, notes="Sharp heat"),
    )
    kitchen_taste.upsert_profile(
        db,
        TasteProfileUpsert(name="Tomato", sour=0.7, sweet=0.4, umami=0.6, notes="Tames heat"),
    )
    kitchen_taste.create_rule(
        db,
        TasteRuleCreate(
            title="Sour + Sweet tames Spicy",
            primary_taste="spicy",
            pair_tastes="sour,sweet",
            rationale="Sourness and sweetness soften capsaicin burn and boost flavor.",
        ),
    )

    kitchen_service.add_item(db, AddKitchenItemRequest(name="Chili Padi", quantity=5))

    # Only spicy item -> should suggest sour/sweet balance
    missing_analysis = kitchen_taste.analyze_fridge_taste(db, ["Chili Padi"])
    assert any("spicy" in hint.lower() and "sour" in hint.lower() for hint in missing_analysis.missing_balance_hints)

    # Add Tomato (sour + sweet) -> should trigger "Sour + Sweet tames Spicy"
    kitchen_service.add_item(db, AddKitchenItemRequest(name="Tomato", quantity=2))
    paired_analysis = kitchen_taste.analyze_fridge_taste(db, ["Chili Padi", "Tomato"])
    assert any("Sour + Sweet tames Spicy" == r.title for r in paired_analysis.applied_rules)
    rule_id = next(r.rule_id for r in paired_analysis.applied_rules if r.title == "Sour + Sweet tames Spicy")

    recipe = kitchen_service.synthesize_dinner_recipe(db)
    assert rule_id in recipe.applied_rule_ids
    assert any("Sour + Sweet tames Spicy" in note for note in recipe.taste_notes)

    # Feedback learning: "too_spicy" lowers spicy preference and boosts counter-rule weight
    before_palate = kitchen_taste.get_palate(db)
    fb1 = kitchen_taste.record_feedback(
        db,
        TasteFeedbackRequest(
            dish_title=recipe.title,
            ingredients_used=["Chili Padi", "Tomato"],
            applied_rule_ids=[rule_id],
            verdict="too_spicy",
        ),
    )
    assert fb1.palate.spicy < before_palate.spicy
    assert fb1.palate.feedback_count == 1

    # Feedback learning: "loved" boosts applied rule weight
    rules_before = {r.id: r.weight for r in kitchen_taste.list_rules(db)}
    fb2 = kitchen_taste.record_feedback(
        db,
        TasteFeedbackRequest(
            dish_title=recipe.title,
            ingredients_used=["Chili Padi", "Tomato"],
            applied_rule_ids=[rule_id],
            verdict="loved",
        ),
    )
    rules_after = {r.id: r.weight for r in kitchen_taste.list_rules(db)}
    assert rules_after[rule_id] > rules_before[rule_id]
    assert fb2.palate.feedback_count == 2

    # Pair affinity & feedback log
    post_analysis = kitchen_taste.analyze_fridge_taste(db, ["Chili Padi", "Tomato"])
    assert any(
        {p.ingredient_a, p.ingredient_b} == {"chili padi", "tomato"} and p.affinity_score > 0
        for p in post_analysis.top_pair_affinities
    )
    assert len(kitchen_taste.list_feedback_logs(db)) == 2


def test_kitchen_auto_profile_cooked_deduct_staples_and_shopping_list():
    db = _make_session()
    kitchen_service.add_item(db, AddKitchenItemRequest(name="Tomato", quantity=2))
    kitchen_service.add_item(
        db,
        AddKitchenItemRequest(name="Soy Sauce", quantity=1, days_old=30, shelf_life_days=7, is_staple=True),
    )

    items = kitchen_service.list_items(db)
    soy = next(i for i in items if i.name == "Soy Sauce")
    assert soy.is_staple is True
    assert soy.is_expiring_soon is False

    # Auto-profile unknown fridge items (works with heuristic fallback if Ollama is offline)
    auto = kitchen_taste.auto_profile_unknown_ingredients(db, ["Tomato", "Soy Sauce"])
    assert len(auto.created_profiles) == 2
    tom = next(p for p in auto.created_profiles if p.name == "Tomato")
    assert tom.sour > 0 and tom.texture != ""

    # Mark cooked -> deducts Tomato (2 -> 1), skips staple Soy Sauce
    cooked = kitchen_service.mark_meal_cooked(
        db,
        CookedMealRequest(dish_title="Tomato Stir Fry", ingredients_used=["Tomato", "Soy Sauce"], deduct_qty=1.0),
    )
    assert any("Tomato" in d for d in cooked.deducted_items)
    assert "Soy Sauce" in cooked.skipped_staples

    # Shopping list CRUD
    sl_item = kitchen_service.add_shopping_item(
        db,
        ShoppingListCreate(name="Calamansi Lime", reason="Add sour balance"),
    )
    assert sl_item.is_checked is False
    toggled = kitchen_service.toggle_shopping_item(db, sl_item.id)
    assert toggled is not None and toggled.is_checked is True
    assert kitchen_service.delete_shopping_item(db, sl_item.id) is True


def test_assistant_ingest_routing_and_morning_briefing(monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "groq_api_key", "")
    monkeypatch.setattr(app_settings, "nvidia_api_key", "")
    db = _make_session()
    sched = assistant_service.ingest_message(
        db,
        AssistantIngestRequest(raw_message="Dinner with Alex tomorrow 8pm", source="whatsapp", sender="Me"),
    )[0]
    assert sched.category == "schedule"
    assert "20:00" in sched.scheduled_for
    assert sched.ai_plan != ""

    groc = assistant_service.ingest_message(
        db,
        AssistantIngestRequest(raw_message="Buy tomato and chicken breast", source="whatsapp", sender="Me"),
    )[0]
    assert groc.category == "kitchen"
    assert groc.routed_to == "kitchen_shopping"
    sl_names = [s.name.lower() for s in kitchen_service.list_shopping_items(db)]
    assert any("tomato" in n for n in sl_names)

    badminton = assistant_service.ingest_message(
        db,
        AssistantIngestRequest(
            raw_message="Pusat Komuniti SRI PETALING\nRM18\n21 / 11 / 26 Saturday\n8pm - 10pm",
            source="whatsapp",
            sender="Raphael",
        ),
    )[0]
    assert badminton.category == "schedule"
    assert badminton.scheduled_for == "2026-11-21 20:00"

    briefing = assistant_service.get_morning_briefing(db)
    assert len(briefing.pending_tasks) == 3
    assert len(briefing.scheduled_items) == 2
    assert briefing.ai_focus_plan != ""

    # Image attachment ingestion + re-analyze
    import base64
    import io
    from PIL import Image, ImageDraw

    monkeypatch.setattr(app_settings, "gemini_api_key", "")
    img = Image.new("RGB", (320, 120), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 40), "NET TOTAL RM 185.85", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    img_item = assistant_service.ingest_message(
        db,
        AssistantIngestRequest(
            raw_message="Pay christie",
            source="whatsapp",
            sender="Me",
            image_base64=b64,
            image_mime="image/png",
        ),
    )[0]
    assert img_item.cost_tier == "high_cost"
    assert img_item.attachment_data_url.startswith("data:image/png;base64,")
    assert "[Attached Image/Receipt]:" in img_item.raw_message
    reanalyzed = assistant_service.reanalyze_item(db, img_item.id)
    assert reanalyzed is not None and reanalyzed.id == img_item.id


def test_friction_i_owe_auto_repayment_detection():
    db = _make_session()
    debt = friction_service.create_debt(
        db,
        CreateDebtRequest(
            event_name="Personal Loan",
            borrower_name="Lim Shu Ting",
            amount_myr=2000.00,
            direction="i_owe",
            monthly_installment_myr=83.40,
        ),
    )
    assert debt.remaining_myr == 2000.00
    assert debt.direction == "i_owe"

    # Two monthly transfers to LIM SHU TING in Wealth Ledger
    for idx in range(2):
        wealth_service.add_manual_entry(
            db,
            ManualLedgerRequest(
                merchant=f"DuitNow Transfer LIM SHU TING #{idx+1}",
                category="P2P Transfer Out",
                tx_type="expense",
                amount_myr=83.40,
                occurred_at=datetime.now(timezone.utc),
            ),
        )

    summary = friction_service.get_summary(db)
    lim_debt = next(d for d in summary.unsettled_debts if d.id == debt.id)
    assert lim_debt.paid_myr == 166.80
    assert lim_debt.remaining_myr == 1833.20
    assert len(lim_debt.auto_matched_payments) == 2
    assert summary.total_i_owe_myr == 1833.20

    # Manual payoff of the remaining balance auto-settles the debt
    paid = friction_service.pay_debt(db, debt.id, 1833.20)
    assert paid.settled is True
    assert paid.remaining_myr == 0.0
    assert friction_service.get_summary(db).total_i_owe_myr == 0.0


def test_debt_interest_calculator_backfill_tolerance_and_exclude():
    db = _make_session()
    # Loan calculator: reducing vs flat
    red = friction_service.calculate_loan(2000.0, 6.0, 24, "reducing")
    flat = friction_service.calculate_loan(2000.0, 6.0, 24, "flat")
    assert len(red.schedule) == 24 and red.schedule[-1].balance_myr == 0.0
    assert flat.total_interest_myr == 240.0
    assert flat.total_payable_myr == 2240.0
    assert flat.effective_apr_pct > flat.annual_rate_pct

    # Backfill from 60 days ago, with match_installment_only=True
    past_dt = datetime.now(timezone.utc) - timedelta(days=30)
    installment_entry = wealth_service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="DuitNow LIM SHU TING",
            category="P2P Transfer Out",
            tx_type="expense",
            amount_myr=83.40,
            occurred_at=past_dt,
        ),
    )
    dinner_entry = wealth_service.add_manual_entry(
        db,
        ManualLedgerRequest(
            merchant="DuitNow LIM SHU TING Dinner",
            category="Food & Beverage",
            tx_type="expense",
            amount_myr=25.00,
            occurred_at=datetime.now(timezone.utc),
        ),
    )

    debt = friction_service.create_debt(
        db,
        CreateDebtRequest(
            event_name="Loan with Interest",
            borrower_name="Lim Shu Ting",
            amount_myr=2000.00,
            direction="i_owe",
            monthly_installment_myr=83.40,
            start_date=date.today() - timedelta(days=60),
            due_day=25,
            match_installment_only=True,
            interest_type="flat",
            interest_rate_pct=5.0,
            tenure_months=24,
        ),
    )
    # Only the RM 83.40 backfill matches (RM 25.00 dinner ignored)
    assert debt.paid_myr == 83.40
    assert len(debt.auto_matched_payments) == 1
    assert debt.total_interest_myr == 200.00
    assert debt.total_payable_myr == 2200.00
    assert debt.months_left is not None and debt.months_left > 0

    # Excluding the matched entry removes it from paid_myr
    excluded = friction_service.exclude_debt_entry(db, debt.id, installment_entry.id)
    assert excluded.paid_myr == 0.0
    assert len(excluded.auto_matched_payments) == 0

    # Update debt due_day
    updated = friction_service.update_debt(db, debt.id, UpdateDebtRequest(due_day=15))
    assert updated.due_day == 15
    assert dinner_entry.id is not None


def test_assistant_multi_intent_classification_and_bank_statement_import(monkeypatch):
    import base64
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "groq_api_key", "")
    monkeypatch.setattr(app_settings, "nvidia_api_key", "")
    monkeypatch.setattr(app_settings, "gemini_api_key", "")

    db = _make_session()

    pay_item = assistant_service.ingest_message(
        db,
        AssistantIngestRequest(raw_message="Pay Christie RM 45 for lunch", source="whatsapp", sender="Me"),
    )[0]
    assert pay_item.category == "pay"

    plan_item = assistant_service.ingest_message(
        db,
        AssistantIngestRequest(raw_message="Plan a 3-day Penang road trip itinerary", source="whatsapp", sender="Me"),
    )[0]
    assert plan_item.category == "planning"

    msg_q = assistant_service.ingest_message(
        db,
        AssistantIngestRequest(
            raw_message="My colleague texted 'We need to talk Monday' - what does that message mean?",
            source="whatsapp",
            sender="Me",
        ),
    )[0]
    assert msg_q.category == "question"
    assert msg_q.whatsapp_reply != ""
    assert "Reply by:" in msg_q.whatsapp_reply

    typo_q = assistant_service.ingest_message(
        db,
        AssistantIngestRequest(raw_message="How is hitler", source="whatsapp", sender="Me"),
    )[0]
    assert typo_q.category == "question"
    assert typo_q.cost_tier == "medium_cost"
    assert "Reply by:" in typo_q.whatsapp_reply

    math_q = assistant_service.ingest_message(
        db,
        AssistantIngestRequest(
            raw_message="Solve 2x + 5 = 19 step by step",
            source="whatsapp",
            sender="Me",
        ),
    )[0]
    assert math_q.category == "question"
    assert math_q.cost_tier == "high_cost"
    assert math_q.whatsapp_reply != ""
    assert "7" in math_q.ai_plan

    csv_bytes = (
        "Date,Description,Debit,Credit\n"
        "02/11/2026,DuitNow QR Jaya Grocer,45.80,\n"
        "05/11/2026,AI Freelance Wise Payout,,3200.00\n"
    ).encode("utf-8")
    stmt_item = assistant_service.ingest_message(
        db,
        AssistantIngestRequest(
            raw_message="",
            source="whatsapp",
            sender="Me",
            document_base64=base64.b64encode(csv_bytes).decode("ascii"),
            document_filename="ryt_nov.csv",
            document_mime="text/csv",
        ),
    )[0]
    assert stmt_item.category == "bank_statement"
    assert stmt_item.routed_to == "wealth_ledger"
    assert stmt_item.status == "done"
    assert "Inserted +2" in stmt_item.ai_plan
    assert stmt_item.whatsapp_reply != ""


def test_assistant_quota_429_cross_tier_fallback(monkeypatch):
    from app.config import settings as app_settings
    from app.modules.assistant import ai_planner

    ai_planner._MODEL_COOLDOWN_UNTIL.clear()
    monkeypatch.setattr(app_settings, "groq_api_key", "gsk_test_dummy")
    monkeypatch.setattr(app_settings, "nvidia_api_key", "")
    monkeypatch.setattr(app_settings, "gemini_api_key", "")

    tried_models: list[str] = []

    class _FakeResp:
        def __init__(self, status_code: int, payload: dict | None = None, headers: dict | None = None):
            self.status_code = status_code
            self._payload = payload or {}
            self.headers = headers or {}

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, *args, **kwargs):
            return _FakeResp(404)

        def post(self, url, headers=None, json=None, **kwargs):
            model = (json or {}).get("model", "")
            tried_models.append(model)
            # Simulate medium_cost + high_cost Groq quota exhausted (429), low_cost succeeds
            if model in (app_settings.groq_model, "qwen/qwen3.8-27b", "openai/gpt-oss-120b"):
                return _FakeResp(429, headers={"retry-after": "120"})
            return _FakeResp(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"plans": [{"merge_into_id": null, "category": "question", '
                                    '"title": "Who is Hitler?", "ai_plan": "Adolf Hitler was...", '
                                    '"scheduled_for": "", "priority": "normal", "kitchen_items": []}]}'
                                )
                            }
                        }
                    ]
                },
            )

    monkeypatch.setattr(ai_planner.httpx, "Client", _FakeClient)
    plans = ai_planner.analyze_messages("How is hitler", sender="Me", recent_pending=[])
    assert len(plans) == 1
    assert plans[0]["category"] == "question"
    assert plans[0]["cost_tier"] == "low_cost"
    assert plans[0]["engine"] == "groq:gpt-oss-20b"
    assert ("groq", app_settings.groq_model) in ai_planner._MODEL_COOLDOWN_UNTIL
    assert "openai/gpt-oss-120b" in tried_models
    assert "openai/gpt-oss-20b" in tried_models
    ai_planner._MODEL_COOLDOWN_UNTIL.clear()


def test_assistant_whole_app_asset_debt_and_kitchen_matrix(monkeypatch):
    from app.config import settings as app_settings
    from app.modules.assets import service as assets_service
    from app.modules.friction import service as friction_service
    from app.modules.kitchen.schemas import AddKitchenItemRequest

    monkeypatch.setattr(app_settings, "groq_api_key", "")
    monkeypatch.setattr(app_settings, "nvidia_api_key", "")
    monkeypatch.setattr(app_settings, "gemini_api_key", "")
    db = _make_session()

    asset_res = assistant_service.ingest_message(
        db,
        AssistantIngestRequest(raw_message="I bought a Honda Civic for RM120000", source="whatsapp", sender="Me"),
    )[0]
    assert asset_res.routed_to == "asset_tracker"
    assert asset_res.status == "done"
    assert "Added to Assets" in asset_res.whatsapp_reply
    assert any("Honda Civic" in a.name and a.purchase_price_myr == 120000.0 for a in assets_service.list_assets(db))

    debt_res = assistant_service.ingest_message(
        db,
        AssistantIngestRequest(
            raw_message="I took a RM80000 car loan from Public Bank at 3.2% flat for 7 years",
            source="whatsapp",
            sender="Me",
        ),
    )[0]
    assert debt_res.routed_to == "debt_tracker"
    assert debt_res.status == "done"
    assert "Added to Debt Tracker" in debt_res.whatsapp_reply
    debts = friction_service.get_summary(db).unsettled_debts
    assert any(
        d.borrower_name == "Public Bank" and d.amount_myr == 80000.0 and d.interest_rate_pct == 3.2 and d.tenure_months == 84
        for d in debts
    )

    kitchen_service.add_item(
        db,
        AddKitchenItemRequest(name="Chicken Thigh", quantity=2, unit="pcs", days_until_expiry=2),
    )
    craving_res = assistant_service.ingest_message(
        db,
        AssistantIngestRequest(raw_message="I want to eat something crispy", source="whatsapp", sender="Me"),
    )[0]
    assert craving_res.category == "question"
    assert "Chicken Thigh" in craving_res.ai_plan
    assert "crispy" in craving_res.ai_plan.lower()
    assert craving_res.whatsapp_reply != ""


def test_whatsapp_receipt_image_updates_kitchen_matrix_and_wealth_ledger(monkeypatch):
    import base64
    from app.modules.assistant import ai_planner
    from app.modules.wealth import service as wealth_service

    db = _make_session()
    monkeypatch.setattr(
        ai_planner,
        "extract_receipt_details",
        lambda b64, mime="image/jpeg": {
            "is_receipt": True,
            "merchant": "Jaya Grocer",
            "date": "2026-11-22",
            "total_myr": 24.40,
            "account_name": "Other / Cash",
            "items": [
                {"name": "Chicken Breast", "qty": 1, "unit": "pack", "price_myr": 14.50, "is_food": True, "days_until_expiry": 4},
                {"name": "Dishwashing Liquid", "qty": 1, "unit": "bottle", "price_myr": 9.90, "is_food": False, "days_until_expiry": 0},
            ],
        },
    )

    dummy_img_b64 = base64.b64encode(b"fake-receipt-bytes").decode("ascii")
    res = assistant_service.ingest_message(
        db,
        AssistantIngestRequest(
            raw_message="",
            source="whatsapp",
            sender="Me",
            image_base64=dummy_img_b64,
            image_mime="image/jpeg",
        ),
    )[0]
    assert res.status == "done"
    assert res.routed_to == "wealth_ledger+kitchen"
    assert "Chicken Breast" in res.whatsapp_reply

    fridge_names = [i.name for i in kitchen_service.list_items(db)]
    assert any("Chicken Breast" in n for n in fridge_names)
    assert not any("Dishwashing" in n for n in fridge_names)

    entries = wealth_service.list_entries(db)
    assert len(entries) == 1
    assert entries[0].merchant == "Jaya Grocer"
    assert entries[0].amount_myr == 24.40
    assert len(entries[0].receipt_items) == 2
    assert entries[0].added_to_kitchen == ["Chicken Breast"]

