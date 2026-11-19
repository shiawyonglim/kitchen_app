from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    database_url: str = "sqlite:///./personal_erp.db"
    email_mode: str = "demo"  # "demo" or "imap"
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_user: str = ""
    imap_app_password: str = ""
    carousell_mode: str = "live"  # "live" or "demo"

    # Authentication & Security
    session_ttl_hours: int = 24
    session_remember_days: int = 14
    max_failed_login_attempts: int = 5
    lockout_minutes: int = 15
    rate_limit_window_seconds: int = 300
    rate_limit_max_attempts: int = 15
    allow_public_signup: bool = False
    cookie_secure: bool | None = None

    # Google OAuth 2.0 + Gmail API
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""

    # Personal Assistant AI & WhatsApp Bridge
    gemini_api_key: str = ""
    groq_api_key: str = ""
    groq_model: str = "qwen/qwen3.8-27b"
    whatsapp_bridge_token: str = ""
    whatsapp_bridge_user_id: int = 1

    # Mobile Widget (Scriptable) & Push Notifications (ntfy)
    widget_api_token: str = ""
    widget_user_id: int = 1
    ntfy_topic_url: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()

