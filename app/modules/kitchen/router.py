from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.kitchen import ocr, service
from app.modules.kitchen.schemas import (
    AddKitchenItemRequest,
    DinnerRecipeOut,
    IngestReceiptRequest,
    KitchenItemOut,
    NutritionTargetOut,
    ScanReceiptOut,
)

router = APIRouter(prefix="/api/kitchen", tags=["Kitchen Matrix"])


@router.get("/items", response_model=list[KitchenItemOut])
def list_items(db: Session = Depends(get_db)):
    return service.list_items(db)


@router.post("/items", response_model=KitchenItemOut)
def add_item(req: AddKitchenItemRequest, db: Session = Depends(get_db)):
    return service.add_item(db, req)


@router.patch("/items/{item_id}/adjust")
def adjust_item(item_id: int, delta: float, db: Session = Depends(get_db)):
    updated = service.adjust_item_quantity(db, item_id, delta)
    return {"item": updated, "removed": updated is None}


@router.delete("/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    if not service.consume_item(db, item_id):
        raise HTTPException(status_code=404, detail="Kitchen item not found")
    return {"deleted": True}


@router.post("/ingest-receipt", response_model=list[KitchenItemOut])
def ingest_receipt(req: IngestReceiptRequest, db: Session = Depends(get_db)):
    return service.ingest_receipt_text(db, req.source, req.raw_text)


@router.post("/scan-receipt", response_model=ScanReceiptOut)
async def scan_receipt_photo(
    file: UploadFile = File(...),
    source: str = Form("NSK Thermal Camera"),
    db: Session = Depends(get_db),
):
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image upload")
    lines = ocr.extract_receipt_lines(image_bytes)
    added = service.ingest_receipt_text(db, source, "\n".join(lines)) if lines else []
    return ScanReceiptOut(extracted_lines=lines, added_items=added)


@router.get("/target", response_model=NutritionTargetOut)
def get_today_target(db: Session = Depends(get_db)):
    return service.get_today_target(db)


@router.post("/recipe", response_model=DinnerRecipeOut)
def synthesize_recipe(db: Session = Depends(get_db)):
    return service.synthesize_dinner_recipe(db)

