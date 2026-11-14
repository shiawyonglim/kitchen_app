from datetime import datetime
from pydantic import BaseModel, Field

from app.modules.kitchen.schemas import NutritionTargetOut


class LogWorkoutRequest(BaseModel):
    exercise_type: str = Field(..., min_length=1, max_length=64)  # e.g. "Dip Bar" or "5K Run"
    reps: int = Field(default=0, ge=0)
    distance_km: float = Field(default=0.0, ge=0)
    duration_min: float = Field(default=0.0, ge=0)
    calories_burned: int | None = Field(default=None, ge=0)
    avg_velocity_mps: float = Field(default=0.0, ge=0)
    sensor_source: str = Field(default="Forma Optical-Inertial", max_length=64)
    notes: str = ""


class WorkoutOut(BaseModel):
    id: int
    exercise_type: str
    reps: int
    distance_km: float
    duration_min: float
    calories_burned: int
    avg_velocity_mps: float
    sensor_source: str
    notes: str
    logged_at: datetime

    model_config = {"from_attributes": True}


class LogWorkoutResponse(BaseModel):
    workout: WorkoutOut
    kitchen_target: NutritionTargetOut

