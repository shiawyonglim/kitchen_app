from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.assets import service
from app.modules.assets.schemas import AssetOut, CreateAssetRequest, NetWorthSummary, ParseListingsRequest, ValuationOut
from app.modules.assets.scraper import CarousellBlockedError

router = APIRouter(prefix="/api/assets", tags=["Asset Tracker"])


@router.get("", response_model=list[AssetOut])
def list_assets(db: Session = Depends(get_db)):
    return service.list_assets(db)


@router.post("", response_model=AssetOut)
def create_asset(req: CreateAssetRequest, db: Session = Depends(get_db)):
    try:
        return service.create_asset(db, req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{asset_id}")
def delete_asset(asset_id: int, db: Session = Depends(get_db)):
    if not service.delete_asset(db, asset_id):
        raise HTTPException(status_code=404, detail="Asset not found")
    return {"deleted": True}


@router.post("/scrape-all", response_model=list[AssetOut])
def scrape_all(db: Session = Depends(get_db)):
    try:
        return service.scrape_all_assets(db)
    except CarousellBlockedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{asset_id}/scrape", response_model=ValuationOut)
def scrape_one(asset_id: int, db: Session = Depends(get_db)):
    try:
        return service.scrape_asset(db, asset_id)
    except CarousellBlockedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{asset_id}/parse-listings", response_model=ValuationOut)
def parse_listings(asset_id: int, req: ParseListingsRequest, db: Session = Depends(get_db)):
    try:
        return service.save_pasted_valuation(db, asset_id, req.raw_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{asset_id}/history", response_model=list[ValuationOut])
def get_history(asset_id: int, db: Session = Depends(get_db)):
    return service.get_valuation_history(db, asset_id)


@router.get("/net-worth", response_model=NetWorthSummary)
def get_net_worth(db: Session = Depends(get_db)):
    return service.get_net_worth(db)

