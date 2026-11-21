from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.modules.assets.ai_valuator import AIValuationError, AIValuationResult, lookup_asset_valuation
from app.modules.assets.models import AssetValuation, TrackedAsset
from app.modules.assets.schemas import AssetOut, CreateAssetRequest, NetWorthSummary, ValuationOut
from app.modules.assets.scraper import parse_pasted_listings
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
    data = req.model_dump()
    data["name"] = data["name"].strip()
    data["search_query"] = (data.get("search_query") or "").strip() or data["name"]
    existing = db.scalar(select(TrackedAsset).where(TrackedAsset.name == data["name"]))
    if existing:
        raise ValueError(f"Asset '{data['name']}' already exists.")
    asset = TrackedAsset(**data)
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


def _to_valuation_out(val: AssetValuation) -> ValuationOut:
    raw = val.raw_listings if isinstance(val.raw_listings, dict) else {}
    raw_citations = raw.get("citations") if isinstance(raw.get("citations"), list) else []
    citations = [
        {"title": str(c.get("title") or ""), "url": str(c.get("url") or "")}
        for c in raw_citations
        if isinstance(c, dict) and str(c.get("url") or "").startswith(("http://", "https://"))
    ]
    raw_sources = raw.get("source_names") if isinstance(raw.get("source_names"), list) else []
    source_names = [str(s).strip() for s in raw_sources if str(s).strip()]
    return ValuationOut(
        id=val.id,
        median_price_myr=val.median_price_myr,
        min_price_myr=val.min_price_myr,
        max_price_myr=val.max_price_myr,
        sample_count=val.sample_count,
        source=val.source,
        scraped_at=val.scraped_at,
        summary=str(raw.get("summary") or ""),
        citations=citations,
        source_names=source_names,
    )


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
        latest_valuation=_to_valuation_out(latest) if latest else None,
        total_liquid_value_myr=total_val,
        unrealized_pnl_myr=round(total_val - cost_basis, 2),
    )


def list_assets(db: Session) -> list[AssetOut]:
    assets = list(
        db.scalars(select(TrackedAsset).options(selectinload(TrackedAsset.valuations)).order_by(TrackedAsset.id)).all()
    )
    return [_to_asset_out(a) for a in assets]


def get_valuation_history(db: Session, asset_id: int) -> list[ValuationOut]:
    rows = list(
        db.scalars(
            select(AssetValuation)
            .where(AssetValuation.asset_id == asset_id)
            .order_by(AssetValuation.scraped_at.asc())
        ).all()
    )
    return [_to_valuation_out(v) for v in rows]


def _build_valuation_row(asset_id: int, res: AIValuationResult) -> AssetValuation:
    return AssetValuation(
        asset_id=asset_id,
        median_price_myr=res.median_price_myr,
        min_price_myr=res.min_price_myr,
        max_price_myr=res.max_price_myr,
        sample_count=res.sample_count,
        source=res.source,
        raw_listings={
            "filtered_prices": res.filtered_prices,
            "raw_prices": res.raw_prices,
            "summary": res.summary,
            "citations": res.citations,
            "source_names": res.source_names,
        },
    )


def refresh_asset(db: Session, asset_id: int) -> ValuationOut:
    asset = db.get(TrackedAsset, asset_id)
    if not asset:
        raise ValueError("Asset not found")
    res = lookup_asset_valuation(
        name=asset.name,
        search_query=asset.search_query,
        condition=asset.condition,
        category=asset.category,
    )
    val = _build_valuation_row(asset.id, res)
    db.add(val)
    db.commit()
    db.refresh(val)
    return _to_valuation_out(val)


scrape_asset = refresh_asset


def save_pasted_valuation(db: Session, asset_id: int, raw_text: str) -> ValuationOut:
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
        raw_listings={
            "filtered_prices": res.filtered_prices,
            "raw_prices": res.raw_prices,
            "summary": res.summary,
            "citations": res.citations,
            "source_names": res.source_names,
        },
    )
    db.add(val)
    db.commit()
    db.refresh(val)
    return _to_valuation_out(val)


def refresh_all_assets(db: Session) -> list[AssetOut]:
    assets = list(db.scalars(select(TrackedAsset).order_by(TrackedAsset.id)).all())
    if not assets:
        return []
    specs = [(a.id, a.name, a.search_query, a.condition, a.category) for a in assets]
    saved_count = 0
    errors: list[str] = []

    for aid, name, query, cond, cat in specs:
        try:
            res = lookup_asset_valuation(name, query, cond, cat)
            db.add(_build_valuation_row(aid, res))
            db.commit()
            saved_count += 1
        except Exception as exc:
            errors.append(str(exc))

    if saved_count == 0 and errors:
        raise AIValuationError(errors[0])

    return list_assets(db)


scrape_all_assets = refresh_all_assets


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
