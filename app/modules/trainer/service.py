from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.kitchen import service as kitchen_service
from app.modules.trainer.models import WorkoutLog
from app.modules.trainer.schemas import LogWorkoutRequest, LogWorkoutResponse, WorkoutOut


def log_workout(db: Session, req: LogWorkoutRequest) -> LogWorkoutResponse:
    """Persist workout and directly trigger Kitchen recovery macro adjustments in memory."""
    calories = req.calories_burned
    if calories is None:
        if req.distance_km > 0:
            calories = int(round(req.distance_km * 75.0))
        elif req.reps > 0:
            calories = int(round(req.reps * 2.5 + 40))
        else:
            calories = 150

    is_sub30_5k = req.distance_km >= 5.0 and 0 < req.duration_min < 30.0
    if is_sub30_5k:
        bonus_cal = max(calories, 450)
        bonus_prot = 36  # +2 eggs (12g) + 1 can tuna (24g)
        note = f"Sub-30 5K ({req.duration_min:.1f} min, {calories} kcal burned) — heavy recovery fuel"
    elif calories >= 300 or req.reps >= 30:
        bonus_cal = calories
        bonus_prot = 30
        note = f"{req.exercise_type} ({req.reps} reps / {calories} kcal) — muscle recovery boost"
    else:
        bonus_cal = calories
        bonus_prot = 18
        note = f"{req.exercise_type} ({calories} kcal) — light recovery"

    workout = WorkoutLog(
        exercise_type=req.exercise_type,
        reps=req.reps,
        distance_km=req.distance_km,
        duration_min=req.duration_min,
        calories_burned=calories,
        avg_velocity_mps=req.avg_velocity_mps,
        sensor_source=req.sensor_source,
        notes=req.notes or note,
    )
    db.add(workout)
    db.commit()
    db.refresh(workout)

    # Direct Modular Monolith in-memory call to Kitchen Matrix
    kitchen_target = kitchen_service.apply_workout_recovery(
        db,
        bonus_calories=bonus_cal,
        bonus_protein_g=bonus_prot,
        workout_note=note,
    )

    return LogWorkoutResponse(
        workout=WorkoutOut.model_validate(workout),
        kitchen_target=kitchen_target,
    )


def list_workouts(db: Session) -> list[WorkoutOut]:
    rows = list(db.scalars(select(WorkoutLog).order_by(WorkoutLog.logged_at.desc())).all())
    return [WorkoutOut.model_validate(r) for r in rows]


def delete_workout(db: Session, workout_id: int) -> bool:
    row = db.get(WorkoutLog, workout_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True

