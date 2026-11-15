from datetime import datetime
from pydantic import BaseModel, Field


class AuthStatusResponse(BaseModel):
    authenticated: bool
    needs_setup: bool
    allow_public_signup: bool
    google_oauth_enabled: bool = False
    google_connected: bool = False
    user: "UserOut | None" = None


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    last_login_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RegisterRequest(BaseModel):
    full_name: str = Field(default="Owner", min_length=1, max_length=120)
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=12, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)
    remember_me: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=12, max_length=128)


class AuditLogOut(BaseModel):
    id: int
    event_type: str
    email_masked: str
    ip_address: str
    detail: str
    created_at: datetime

    model_config = {"from_attributes": True}

