from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.kitchen import ocr, service, taste
from app.modules.kitchen.schemas import (
    AddKitchenItemRequest,
    AutoProfileOut,
    CookedMealOut,
    CookedMealRequest,
    DinnerRecipeOut,
    IngestReceiptRequest,
    KitchenItemOut,
    NutritionTargetOut,
    ScanReceiptOut,
    ShoppingListCreate,
    ShoppingListItemOut,
    TasteAnalysisOut,
    TasteFeedbackLogOut,
    TasteFeedbackOut,
    TasteFeedbackRequest,
    TasteProfileOut,
    TasteProfileUpsert,
    TasteRuleCreate,
    TasteRuleOut,
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


@router.get("/taste/profiles", response_model=list[TasteProfileOut])
def list_taste_profiles(db: Session = Depends(get_db)):
    return taste.list_profiles(db)


@router.post("/taste/profiles", response_model=TasteProfileOut)
def upsert_taste_profile(req: TasteProfileUpsert, db: Session = Depends(get_db)):
    return taste.upsert_profile(db, req)


@router.delete("/taste/profiles/{profile_id}")
def delete_taste_profile(profile_id: int, db: Session = Depends(get_db)):
    if not taste.delete_profile(db, profile_id):
        raise HTTPException(status_code=404, detail="Taste profile not found")
    return {"deleted": True}


@router.get("/taste/rules", response_model=list[TasteRuleOut])
def list_taste_rules(db: Session = Depends(get_db)):
    return taste.list_rules(db)


@router.post("/taste/rules", response_model=TasteRuleOut)
def create_taste_rule(req: TasteRuleCreate, db: Session = Depends(get_db)):
    return taste.create_rule(db, req)


@router.delete("/taste/rules/{rule_id}")
def delete_taste_rule(rule_id: int, db: Session = Depends(get_db)):
    if not taste.delete_rule(db, rule_id):
        raise HTTPException(status_code=404, detail="Taste rule not found")
    return {"deleted": True}


@router.get("/taste/analysis", response_model=TasteAnalysisOut)
def get_taste_analysis(db: Session = Depends(get_db)):
    items = service.list_items(db)
    return taste.analyze_fridge_taste(db, [i.name for i in items if i.quantity > 0])


@router.post("/taste/feedback", response_model=TasteFeedbackOut)
def submit_taste_feedback(req: TasteFeedbackRequest, db: Session = Depends(get_db)):
    return taste.record_feedback(db, req)


@router.get("/taste/feedback", response_model=list[TasteFeedbackLogOut])
def list_taste_feedback(db: Session = Depends(get_db)):
    return taste.list_feedback_logs(db)


@router.post("/taste/auto-profile", response_model=AutoProfileOut)
def auto_profile_fridge_items(db: Session = Depends(get_db)):
    items = service.list_items(db)
    return taste.auto_profile_unknown_ingredients(db, [i.name for i in items if i.quantity > 0])


@router.post("/cooked", response_model=CookedMealOut)
def mark_cooked(req: CookedMealRequest, db: Session = Depends(get_db)):
    return service.mark_meal_cooked(db, req)


@router.get("/shopping-list", response_model=list[ShoppingListItemOut])
def get_shopping_list(db: Session = Depends(get_db)):
    return service.list_shopping_items(db)


@router.post("/shopping-list", response_model=ShoppingListItemOut)
def add_to_shopping_list(req: ShoppingListCreate, db: Session = Depends(get_db)):
    return service.add_shopping_item(db, req)


@router.patch("/shopping-list/{item_id}/toggle", response_model=ShoppingListItemOut)
def toggle_shopping_list_item(item_id: int, db: Session = Depends(get_db)):
    updated = service.toggle_shopping_item(db, item_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Shopping list item not found")
    return updated


@router.delete("/shopping-list/{item_id}")
def delete_shopping_list_item(item_id: int, db: Session = Depends(get_db)):
    if not service.delete_shopping_item(db, item_id):
        raise HTTPException(status_code=404, detail="Shopping list item not found")
    return {"deleted": True}

