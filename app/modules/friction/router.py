from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.friction import service
from app.modules.friction.schemas import (
    CreateDebtRequest,
    FrictionSummary,
    GearOut,
    LogOdometerRequest,
    LogUsageRequest,
    OdometerOut,
    ParseTngSplitRequest,
    SocialDebtOut,
)

router = APIRouter(prefix="/api/friction", tags=["Friction & Social Debt"])


@router.get("/summary", response_model=FrictionSummary)
def get_summary(db: Session = Depends(get_db)):
    return service.get_summary(db)


@router.post("/odometer", response_model=OdometerOut)
def log_odometer(req: LogOdometerRequest, db: Session = Depends(get_db)):
    return service.log_odometer(db, req)


@router.post("/gear/{gear_id}/use", response_model=GearOut)
def use_gear(gear_id: int, req: LogUsageRequest, db: Session = Depends(get_db)):
    try:
        return service.log_gear_usage(db, gear_id, req.amount)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/gear/{gear_id}/reset", response_model=GearOut)
def reset_gear(gear_id: int, db: Session = Depends(get_db)):
    try:
        return service.reset_gear(db, gear_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/debts", response_model=SocialDebtOut)
def create_debt(req: CreateDebtRequest, db: Session = Depends(get_db)):
    return service.create_debt(db, req)


@router.post("/debts/parse-tng", response_model=list[SocialDebtOut])
def parse_tng(req: ParseTngSplitRequest, db: Session = Depends(get_db)):
    return service.parse_tng_split_bill(db, req.event_name, req.raw_text)


@router.post("/debts/{debt_id}/settle", response_model=SocialDebtOut)
def settle_debt(debt_id: int, db: Session = Depends(get_db)):
    try:
        return service.settle_debt(db, debt_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/debts/{debt_id}")
def delete_debt(debt_id: int, db: Session = Depends(get_db)):
    if not service.delete_debt(db, debt_id):
        raise HTTPException(status_code=404, detail="Debt not found")
    return {"deleted": True}


@router.delete("/odometer/{odo_id}")
def delete_odometer(odo_id: int, db: Session = Depends(get_db)):
    if not service.delete_odometer(db, odo_id):
        raise HTTPException(status_code=404, detail="Odometer log not found")
    return {"deleted": True}

