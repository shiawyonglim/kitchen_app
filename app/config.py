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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()

