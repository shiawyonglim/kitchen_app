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

