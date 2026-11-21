from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base
from app.modules.assets import service as asset_service
from app.modules.assets.scraper import filter_outliers
from app.modules.assets.schemas import CreateAssetRequest


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()


def test_outlier_filtering():
    raw = [1.0, 440.0, 460.0, 475.0, 490.0, 510.0, 2500.0]
    filtered = filter_outliers(raw)
    assert 1.0 not in filtered
    assert 2500.0 not in filtered
    assert len(filtered) == 5


def test_seed_scrape_and_net_worth(monkeypatch):
    monkeypatch.setattr(settings, "carousell_mode", "demo")
    db = _make_session()
    asset_service.seed_default_assets(db)

    assets = asset_service.list_assets(db)
    names = {a.name for a in assets}
    assert "Sealed Mega Charizard Box" in names
    assert "Moza R3 Racing Wheel" in names

    updated = asset_service.scrape_all_assets(db)
    assert all(a.latest_valuation is not None for a in updated)

    extra = asset_service.create_asset(
        db,
        CreateAssetRequest(
            name="Yonex Arcsaber 11 Pro",
            search_query="yonex arcsaber 11 pro",
            category="Badminton",
            purchase_price_myr=720.0,
        ),
    )
    assert extra.name == "Yonex Arcsaber 11 Pro"

    nw = asset_service.get_net_worth(db)
    assert nw.asset_count == 3
    assert nw.tracked_assets_value_myr > 0
    assert nw.total_net_worth_myr == round(nw.liquid_cash_myr + nw.tracked_assets_value_myr, 2)


def test_paste_carousell_listings_outlier_filter():
    db = _make_session()
    asset_service.seed_default_assets(db)
    assets = asset_service.list_assets(db)
    charizard = next(a for a in assets if "Charizard" in a.name)

    val = asset_service.save_pasted_valuation(
        db, charizard.id, "RM 1, RM 440, RM 460, RM 475, RM 490, RM 5000"
    )
    assert val.source == "carousell_pasted"
    assert val.median_price_myr == 475.0
    assert val.min_price_myr >= 440.0


def test_gemini_google_search_valuation_parsing(monkeypatch):
    import json
    from app.modules.assets import ai_valuator

    monkeypatch.setattr(settings, "carousell_mode", "live")
    monkeypatch.setattr(settings, "gemini_api_key", "test-gemini-key")
    monkeypatch.setattr(settings, "gemini_search_model", "gemini-3.8-flash")
    monkeypatch.setattr(settings, "groq_api_key", "")
    monkeypatch.setattr(ai_valuator, "_gemini_search_cooldown_until", 0.0)

    class _FakeResp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def _fake_post(self, url, **kwargs):
        assert "generativelanguage.googleapis.com" in url
        return _FakeResp(
            200,
            {
                "steps": [
                    {
                        "type": "model_output",
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "median_price_myr": 900,
                                        "min_price_myr": 800,
                                        "max_price_myr": 1000,
                                        "sample_count": 4,
                                        "found_prices": [800, 850, 950, 1000],
                                        "summary": "Used Moza R3 sells for RM 800-1,000 in MY.",
                                        "sources": ["Carousell MY", "Mudah.my"],
                                    }
                                ),
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "title": "Carousell MY",
                                        "url": "https://www.carousell.com.my/p/moza-r3-123/",
                                    },
                                    {
                                        "type": "url_citation",
                                        "title": "Invalid JS Link",
                                        "url": "javascript:alert(1)",
                                    },
                                ],
                            }
                        ],
                    }
                ]
            },
        )

    monkeypatch.setattr(ai_valuator.httpx.Client, "post", _fake_post)

    res = ai_valuator.lookup_asset_valuation(
        "Moza R3 Racing Wheel", "moza r3", "Used - Mint", "Sim Racing Hardware"
    )
    assert res.source == "ai:gemini-3.8-flash"
    assert res.median_price_myr == 900.0
    assert res.min_price_myr == 800.0
    assert res.max_price_myr == 1000.0
    assert res.sample_count == 4
    assert "RM 800-1,000" in res.summary
    assert res.citations == [
        {"title": "Carousell MY", "url": "https://www.carousell.com.my/p/moza-r3-123/"}
    ]
    assert res.source_names == ["Carousell MY", "Mudah.my"]


def test_gemini_429_falls_back_to_groq_browser_search(monkeypatch):
    import json
    from app.modules.assets import ai_valuator

    monkeypatch.setattr(settings, "carousell_mode", "live")
    monkeypatch.setattr(settings, "gemini_api_key", "test-gemini-key")
    monkeypatch.setattr(settings, "groq_api_key", "test-groq-key")
    monkeypatch.setattr(settings, "groq_search_model", "openai/gpt-oss-120b")
    monkeypatch.setattr(ai_valuator, "_gemini_search_cooldown_until", 0.0)

    calls = []

    class _FakeResp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def _fake_post(self, url, **kwargs):
        calls.append(url)
        if "generativelanguage.googleapis.com" in url:
            return _FakeResp(429, {"error": {"code": 429}})
        assert "api.groq.com" in url
        return _FakeResp(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": "```json\n"
                            + json.dumps(
                                {
                                    "median_price_myr": 925.0,
                                    "min_price_myr": 780.0,
                                    "max_price_myr": 1100.0,
                                    "sample_count": 3,
                                    "found_prices": [780.0, 925.0, 1100.0],
                                    "summary": "Carousell & Mudah MY listings around RM 925.",
                                    "sources": ["Carousell MY"],
                                }
                            )
                            + "\n```",
                            "executed_tools": [
                                {
                                    "type": "browser_search",
                                    "search_results": {
                                        "results": [
                                            {"title": "Blog Post", "url": "https://example.com/moza"},
                                            {
                                                "title": "Moza R3 MY",
                                                "url": "https://www.carousell.com.my/p/moza-r3-999/",
                                            },
                                        ]
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(ai_valuator.httpx.Client, "post", _fake_post)

    res = ai_valuator.lookup_asset_valuation("Moza R3 Racing Wheel", "moza r3", "Used - Mint")
    assert len(calls) == 2
    assert res.source == "ai:gpt-oss-120b"
    assert res.median_price_myr == 925.0
    assert res.citations[0]["url"] == "https://www.carousell.com.my/p/moza-r3-999/"
    assert ai_valuator._gemini_search_cooldown_until > 0.0


def test_create_asset_optional_search_query_and_refresh_endpoint(monkeypatch):
    from app.modules.assets.ai_valuator import AIValuationResult

    db = _make_session()
    created = asset_service.create_asset(
        db,
        CreateAssetRequest(
            name="Nintendo Switch OLED",
            condition="Used - Mint",
            purchase_price_myr=1100.0,
        ),
    )
    assert created.search_query == "Nintendo Switch OLED"
    assert created.condition == "Used - Mint"

    monkeypatch.setattr(
        asset_service,
        "lookup_asset_valuation",
        lambda **kwargs: AIValuationResult(
            median_price_myr=950.0,
            min_price_myr=850.0,
            max_price_myr=1050.0,
            sample_count=5,
            source="ai:gpt-oss-120b",
            filtered_prices=[850.0, 900.0, 950.0, 1000.0, 1050.0],
            raw_prices=[850.0, 900.0, 950.0, 1000.0, 1050.0],
            summary="Used OLED bundles around RM 950 on Carousell MY.",
            citations=[{"title": "Carousell MY", "url": "https://www.carousell.com.my/q/switch-oled"}],
            source_names=["Carousell MY"],
        ),
    )
    val = asset_service.refresh_asset(db, created.id)
    assert val.median_price_myr == 950.0
    assert val.summary == "Used OLED bundles around RM 950 on Carousell MY."
    assert val.citations == [{"title": "Carousell MY", "url": "https://www.carousell.com.my/q/switch-oled"}]

    listed = asset_service.list_assets(db)
    assert listed[0].latest_valuation is not None
    assert listed[0].latest_valuation.summary == "Used OLED bundles around RM 950 on Carousell MY."
    assert listed[0].total_liquid_value_myr == 950.0
    assert listed[0].unrealized_pnl_myr == -150.0


def test_ai_usage_logging_and_summary(monkeypatch):
    import app.database as app_db
    from app.modules.settings import ai_usage
    from app.modules.settings.schemas import UpdateAiModelConfigRequest

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(app_db, "SessionLocal", TestSessionLocal)

    ai_usage.record_ai_usage("groq", "openai/gpt-oss-120b", "asset_search", status="ok", input_tokens=500, output_tokens=150, latency_ms=1200)
    ai_usage.record_ai_usage("gemini", "gemini-3.8-flash", "asset_search", status="rate_limited", latency_ms=200, error_detail="HTTP 429")
    ai_usage.record_ai_usage("nim", "nvidia/nemotron-3.5-super-vl-120b-a12b", "assistant_plan", status="ok", input_tokens=300, output_tokens=100, latency_ms=800)

    with TestSessionLocal() as db:
        summary = ai_usage.get_ai_usage_summary(db, days=30)
        assert summary.total_calls == 3
        assert summary.success_calls == 2
        assert summary.rate_limited_calls == 1
        assert summary.total_tokens == 1050
        by_prov = {p.provider: p for p in summary.providers}
        assert by_prov["groq"].total_tokens == 650
        assert by_prov["gemini"].rate_limited_calls == 1
        by_svc = {s.service: s for s in summary.services}
        assert by_svc["asset_search"].calls == 2
        assert by_svc["assistant_plan"].calls == 1
        assert len(summary.recent_logs) == 3

        # Verify model catalog, capabilities, n/x quotas, and editable limits
        by_model = {(m.provider, m.model): m for m in summary.models}
        assert ("groq", "whisper-large-v3") in by_model
        assert "voice_notes" in by_model[("groq", "whisper-large-v3")].capabilities
        assert "web_search" in by_model[("gemini", "gemini-3.8-flash")].capabilities
        assert "images" in by_model[("nim", "nvidia/nemotron-3.5-super-vl-120b-a12b")].capabilities
        assert by_model[("groq", "openai/gpt-oss-120b")].used_calls == 1

        updated_cfg = ai_usage.upsert_model_config(
            db,
            UpdateAiModelConfigRequest(
                provider="groq",
                model="openai/gpt-oss-120b",
                limit_period="minute",
                req_limit=10,
                tok_limit=2000,
            ),
        )
        assert updated_cfg.limit_period == "minute"
        assert updated_cfg.req_limit == 10
        assert updated_cfg.used_calls == 1
        assert updated_cfg.remaining_calls == 9
        assert updated_cfg.remaining_pct == 67.5

        deleted = ai_usage.clear_ai_usage_logs(db)
        assert deleted == 3
        assert ai_usage.get_ai_usage_summary(db, days=30).total_calls == 0

    # Test model ping helper with mocked httpx.post
    class _FakeTestResp:
        status_code = 200

        def json(self):
            return {"usage": {"total_tokens": 18}}

    monkeypatch.setattr(settings, "groq_api_key", "test-groq")
    monkeypatch.setattr(ai_usage.httpx, "post", lambda *a, **kw: _FakeTestResp())
    ping = ai_usage.test_ai_model("groq", "qwen/qwen3.8-27b")
    assert ping.ok is True
    assert ping.tokens == 18




