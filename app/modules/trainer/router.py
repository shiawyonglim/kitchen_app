from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.trainer import service
from app.modules.trainer.schemas import LogWorkoutRequest, LogWorkoutResponse, WorkoutOut

router = APIRouter(prefix="/api/trainer", tags=["Physical Trainer"])


@router.get("/workouts", response_model=list[WorkoutOut])
def list_workouts(db: Session = Depends(get_db)):
    return service.list_workouts(db)


@router.post("/workouts", response_model=LogWorkoutResponse)
def log_workout(req: LogWorkoutRequest, db: Session = Depends(get_db)):
    return service.log_workout(db, req)


@router.delete("/workouts/{workout_id}")
def delete_workout(workout_id: int, db: Session = Depends(get_db)):
    if not service.delete_workout(db, workout_id):
        raise HTTPException(status_code=404, detail="Workout not found")
    return {"deleted": True}

