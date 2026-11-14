from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.settings import service
from app.modules.settings.schemas import AppSettingsOut, UpdateSettingsRequest

router = APIRouter(prefix="/api/settings", tags=["Settings"])


@router.get("", response_model=AppSettingsOut)
def get_settings(db: Session = Depends(get_db)):
    return service.get_settings(db)


@router.put("", response_model=AppSettingsOut)
def update_settings(req: UpdateSettingsRequest, db: Session = Depends(get_db)):
    return service.update_settings(db, req)


@router.post("/run-auto-sync", response_model=AppSettingsOut)
def run_auto_sync_now(db: Session = Depends(get_db)):
    return service.run_daily_auto_sync(db)

