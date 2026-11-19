import hmac
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db, reset_current_user_id, set_current_user_id
from app.modules.assistant import bridge_manager, service
from app.modules.assistant.schemas import (
    AssistantIngestRequest,
    AssistantItemOut,
    AssistantItemUpdate,
    MorningBriefingOut,
)

router = APIRouter(prefix="/api/assistant", tags=["Personal Assistant"])
bridge_router = APIRouter(prefix="/api/assistant", tags=["WhatsApp Bridge"])


@router.get("/items", response_model=list[AssistantItemOut])
def list_assistant_items(status: str | None = None, db: Session = Depends(get_db)):
    return service.list_items(db, status=status)


@router.post("/ingest", response_model=list[AssistantItemOut])
def ingest_assistant_message(req: AssistantIngestRequest, db: Session = Depends(get_db)):
    return service.ingest_message(db, req)


@router.patch("/items/{item_id}", response_model=AssistantItemOut)
def update_assistant_item(item_id: int, req: AssistantItemUpdate, db: Session = Depends(get_db)):
    updated = service.update_item(db, item_id, req)
    if not updated:
        raise HTTPException(status_code=404, detail="Assistant item not found")
    return updated


@router.delete("/items/{item_id}")
def delete_assistant_item(item_id: int, db: Session = Depends(get_db)):
    if not service.delete_item(db, item_id):
        raise HTTPException(status_code=404, detail="Assistant item not found")
    return {"deleted": True}


@router.get("/morning-briefing", response_model=MorningBriefingOut)
def morning_briefing(db: Session = Depends(get_db)):
    return service.get_morning_briefing(db)


@router.get("/whatsapp-status")
def whatsapp_status():
    return bridge_manager.get_bridge_status()


@router.post("/whatsapp-restart")
def whatsapp_restart(clear_session: bool = False):
    started = bridge_manager.restart_bridge(clear_session=clear_session)
    return {"started": started, **bridge_manager.get_bridge_status()}


@bridge_router.post("/whatsapp-ingest", response_model=list[AssistantItemOut])
def whatsapp_bridge_ingest(
    req: AssistantIngestRequest,
    x_bridge_token: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """Silent webhook for the local WhatsApp Web bridge, authenticated via shared bridge token."""
    expected = settings.whatsapp_bridge_token
    if not expected or not hmac.compare_digest(x_bridge_token, expected):
        raise HTTPException(status_code=401, detail="Invalid or unconfigured WhatsApp bridge token")

    tok = set_current_user_id(settings.whatsapp_bridge_user_id)
    db.info["user_id"] = settings.whatsapp_bridge_user_id
    try:
        return service.ingest_message(db, req)
    finally:
        reset_current_user_id(tok)
