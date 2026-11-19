from datetime import datetime
from pydantic import BaseModel, Field


class AssistantIngestRequest(BaseModel):
    raw_message: str = Field(..., min_length=1, max_length=4000)
    source: str = Field(default="manual", max_length=64)
    sender: str = Field(default="Me", max_length=128)


class AssistantItemUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(pending|done|archived)$")
    scheduled_for: str | None = Field(default=None, max_length=64)
    priority: str | None = Field(default=None, pattern="^(high|normal|low)$")


class AssistantItemOut(BaseModel):
    id: int
    source: str
    sender: str
    raw_message: str
    category: str
    title: str
    ai_plan: str
    scheduled_for: str
    priority: str
    status: str
    routed_to: str
    engine: str
    created_at: datetime


class MorningBriefingOut(BaseModel):
    date_label: str
    greeting: str
    pending_tasks: list[AssistantItemOut]
    scheduled_items: list[AssistantItemOut]
    expiring_kitchen: list[str]
    ai_focus_plan: str
    engine: str
