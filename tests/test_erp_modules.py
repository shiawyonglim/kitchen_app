from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.modules.friction import service as friction_service
from app.modules.friction.schemas import LogOdometerRequest
from app.modules.kitchen import service as kitchen_service
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
    kitchen_service.seed_default_kitchen(db)

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
    kitchen_service.seed_default_kitchen(db)
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
            )
            assert put_resp.status_code == 200
            updated = put_resp.json()
            assert updated["auto_sync_hour"] == 8
            assert updated["monthly_budget_myr"] == 3500.0

            sync_resp = client.post("/api/settings/run-auto-sync")
            assert sync_resp.status_code == 200
            synced = sync_resp.json()
            assert synced["last_auto_sync_at"] is not None
            assert synced["last_auto_sync_status"].startswith("OK:")
    finally:
        app_main.app.dependency_overrides.clear()
        test_engine.dispose()

