from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.settings import ai_usage, service
from app.modules.settings.schemas import (
    AiModelQuotaOut,
    AiUsageSummaryOut,
    AppSettingsOut,
    TestAiModelRequest,
    TestAiModelResponse,
    UpdateAiModelConfigRequest,
    UpdateSettingsRequest,
)

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


@router.get("/ai-usage", response_model=AiUsageSummaryOut)
def get_ai_usage(days: int = Query(default=30, ge=1, le=365), db: Session = Depends(get_db)):
    return ai_usage.get_ai_usage_summary(db, days=days)


@router.delete("/ai-usage")
def clear_ai_usage(db: Session = Depends(get_db)):
    return {"deleted": ai_usage.clear_ai_usage_logs(db)}


@router.put("/ai-models/config", response_model=AiModelQuotaOut)
def update_ai_model_config(req: UpdateAiModelConfigRequest, db: Session = Depends(get_db)):
    return ai_usage.upsert_model_config(db, req)


@router.post("/ai-models/test", response_model=TestAiModelResponse)
def test_ai_model_endpoint(req: TestAiModelRequest):
    return ai_usage.test_ai_model(req.provider, req.model)

