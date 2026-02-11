from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Receipt YNAB Service"
    api_prefix: str = "/api"

    database_url: str = "sqlite:///./data/app.db"
    redis_url: str = "redis://localhost:6379/0"

    ingest_dir: Path = Path("./data/ingest")
    object_store_root: Path = Path("./data")
    object_store_receipts_prefix: str = "receipts"

    scan_interval_seconds: int = 10
    stable_checks_required: int = 2
    stable_min_age_seconds: int = 3

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3-flash-preview"
    gemini_prompt: str = "Categorize receipt line items into the most appropriate YNAB categories."

    ynab_access_token: str | None = None
    ynab_budget_id: str | None = None
    ynab_default_account_id: str | None = None

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, list):
            return value
        if not value:
            return []
        return [origin.strip() for origin in value.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ingest_dir.mkdir(parents=True, exist_ok=True)
    (settings.object_store_root / settings.object_store_receipts_prefix).mkdir(parents=True, exist_ok=True)
    return settings
