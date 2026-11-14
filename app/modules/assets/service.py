from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.assets.models import AssetValuation, TrackedAsset
from app.modules.assets.schemas import AssetOut, CreateAssetRequest, NetWorthSummary, ValuationOut
from app.modules.assets.scraper import CarousellBlockedError, parse_pasted_listings, scrape_carousell_my
from app.modules.wealth import service as wealth_service

DEFAULT_ASSETS = [
    CreateAssetRequest(
        name="Sealed Mega Charizard Box",
        search_query="mega charizard box sealed",
        category="Collectibles",
        condition="Sealed",
        purchase_price_myr=350.0,
        quantity=1,
    ),
    CreateAssetRequest(
        name="Moza R3 Racing Wheel",
        search_query="moza r3",
        category="Sim Racing Hardware",
        condition="Used - Mint",
        purchase_price_myr=1899.0,
        quantity=1,
    ),
]


def seed_default_assets(db: Session) -> None:
    """Ensure the user's core hardware & collectibles exist on first run."""
    existing = set(db.scalars(select(TrackedAsset.name)).all())
    added = False
    for item in DEFAULT_ASSETS:
        if item.name not in existing:
            db.add(TrackedAsset(**item.model_dump()))
            added = True
    if added:
        db.commit()


def create_asset(db: Session, req: CreateAssetRequest) -> AssetOut:
    existing = db.scalar(select(TrackedAsset).where(TrackedAsset.name == req.name))
    if existing:
        raise ValueError(f"Asset '{req.name}' already exists.")
    asset = TrackedAsset(**req.model_dump())
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return _to_asset_out(asset)


def delete_asset(db: Session, asset_id: int) -> bool:
    asset = db.get(TrackedAsset, asset_id)
    if not asset:
        return False
    db.delete(asset)
    db.commit()
    return True


def _to_asset_out(asset: TrackedAsset) -> AssetOut:
    latest = asset.valuations[0] if asset.valuations else None
    unit_val = latest.median_price_myr if latest else asset.purchase_price_myr
    total_val = round(unit_val * asset.quantity, 2)
    cost_basis = round(asset.purchase_price_myr * asset.quantity, 2)
    return AssetOut(
        id=asset.id,
        name=asset.name,
        search_query=asset.search_query,
        category=asset.category,
        condition=asset.condition,
        purchase_price_myr=asset.purchase_price_myr,
        quantity=asset.quantity,
        latest_valuation=ValuationOut.model_validate(latest) if latest else None,
        total_liquid_value_myr=total_val,
        unrealized_pnl_myr=round(total_val - cost_basis, 2),
    )


def list_assets(db: Session) -> list[AssetOut]:
    assets = list(
        db.scalars(select(TrackedAsset).options(selectinload(TrackedAsset.valuations)).order_by(TrackedAsset.id)).all()
    )
    return [_to_asset_out(a) for a in assets]


def get_valuation_history(db: Session, asset_id: int) -> list[AssetValuation]:
    return list(
        db.scalars(
            select(AssetValuation)
            .where(AssetValuation.asset_id == asset_id)
            .order_by(AssetValuation.scraped_at.asc())
        ).all()
    )


def scrape_asset(db: Session, asset_id: int) -> AssetValuation:
    asset = db.get(TrackedAsset, asset_id)
    if not asset:
        raise ValueError("Asset not found")
    res = scrape_carousell_my(asset.search_query)
    val = AssetValuation(
        asset_id=asset.id,
        median_price_myr=res.median_price_myr,
        min_price_myr=res.min_price_myr,
        max_price_myr=res.max_price_myr,
        sample_count=res.sample_count,
        source=res.source,
        raw_listings={"filtered_prices": res.filtered_prices, "raw_prices": res.raw_prices},
    )
    db.add(val)
    db.commit()
    db.refresh(val)
    return val


def save_pasted_valuation(db: Session, asset_id: int, raw_text: str) -> AssetValuation:
    asset = db.get(TrackedAsset, asset_id)
    if not asset:
        raise ValueError("Asset not found")
    res = parse_pasted_listings(raw_text)
    val = AssetValuation(
        asset_id=asset.id,
        median_price_myr=res.median_price_myr,
        min_price_myr=res.min_price_myr,
        max_price_myr=res.max_price_myr,
        sample_count=res.sample_count,
        source=res.source,
        raw_listings={"filtered_prices": res.filtered_prices, "raw_prices": res.raw_prices},
    )
    db.add(val)
    db.commit()
    db.refresh(val)
    return val


def scrape_all_assets(db: Session) -> list[AssetOut]:
    assets = list(db.scalars(select(TrackedAsset)).all())
    for asset in assets:
        scrape_asset(db, asset.id)
    return list_assets(db)


def get_net_worth(db: Session) -> NetWorthSummary:
    """Combine Wealth Ledger liquid cash with Asset Tracker fair-market valuations."""
    wealth = wealth_service.get_summary(db)
    assets = list_assets(db)
    assets_total = round(sum(a.total_liquid_value_myr for a in assets), 2)
    return NetWorthSummary(
        liquid_cash_myr=wealth.net_cash_flow_myr,
        tracked_assets_value_myr=assets_total,
        total_net_worth_myr=round(wealth.net_cash_flow_myr + assets_total, 2),
        asset_count=len(assets),
    )

