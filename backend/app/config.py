from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Receipt YNAB Service"
    api_prefix: str = "/api"

    database_url: str = "sqlite:///./data/app.db"
    redis_url: str = "redis://localhost:6379/0"

    ingest_dir: Path = Path("./data/ingest")
    object_store_root: Path = Path("./data")
    object_store_receipts_prefix: str = "receipts"
    log_file_path: Path = Path("./data/logs/app.log")

    scan_interval_seconds: int = 10
    stable_checks_required: int = 2
    stable_min_age_seconds: int = 3
    max_ingest_file_size_bytes: int = 50 * 1024 * 1024  # 50 MB
    stuck_job_timeout_minutes: int = 30

    game_green_hours_threshold: float = 24.0
    game_brown_hours_threshold: float = 72.0
    game_timezone: str = "UTC"
    game_token_earn_every_greens: int = 5
    game_shred_daily_spend_cap: int = 0
    game_green_ratio_target_percent: int = 70
    game_streak_challenge_target: int = 6
    game_shred_challenge_target: int = 2
    game_water_capacity: int = 15
    game_bucket_capacity: int = 3
    game_fire_burn_threshold: int = 15
    correction_fade_days: int = 90

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3-flash-preview"
    gemini_prompt: str = "Categorize receipt line items into the most appropriate YNAB categories."
    gemini_max_retries: int = 3
    ai_model_registry_path: Path = Path("./shared/receipt_shared/resources/ai_model_registry.v1.json")
    ai_limits_config_path: Path = Path("./config/ai_limits.v1.json")
    ai_usage_db_url: str = "sqlite:///./data/ai_usage.db"
    ai_limit_behavior: str = "hard_fail"
    twin_extraction_enabled: bool = True
    twin_strict_mode: bool = False
    twin_recon_hard_fail_delta_abs: float = 2.00
    twin_recon_hard_fail_delta_pct: float = 0.02

    ynab_access_token: str | None = None
    ynab_budget_id: str | None = None
    ynab_default_account_id: str | None = None
    ynab_cache_refresh_interval_minutes: int = 30
    ynab_reconciliation_interval_hours: int = 12
    ynab_reconciliation_lookback_days: int = 90
    ynab_new_transaction_flag_color: str = "blue"
    ynab_updated_transaction_flag_color: str = "purple"

    debug_tools_enabled: bool = False
    debug_tools_flag_file: Path = Path("./data/debug_tools_enabled.flag")

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    @field_validator("ingest_dir", mode="before")
    @classmethod
    def normalize_ingest_dir(cls, value: str | Path | None) -> str | Path:
        if value is None:
            return Path("./data/ingest")
        if isinstance(value, str) and not value.strip():
            return Path("./data/ingest")
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, list):
            return value
        if not value:
            return []
        return [origin.strip() for origin in value.split(",") if origin.strip()]

    @field_validator("game_green_hours_threshold", "game_brown_hours_threshold")
    @classmethod
    def validate_game_hour_thresholds(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Game hour thresholds must be greater than zero")
        return value

    @field_validator("game_token_earn_every_greens")
    @classmethod
    def validate_token_every(cls, value: int) -> int:
        if value < 1:
            raise ValueError("GAME_TOKEN_EARN_EVERY_GREENS must be at least 1")
        return value

    @field_validator("game_shred_daily_spend_cap")
    @classmethod
    def validate_spend_cap(cls, value: int) -> int:
        if value < 0:
            raise ValueError("GAME_SHRED_DAILY_SPEND_CAP cannot be negative")
        return value

    @field_validator("game_green_ratio_target_percent")
    @classmethod
    def validate_ratio_target(cls, value: int) -> int:
        if value < 1 or value > 100:
            raise ValueError("GAME_GREEN_RATIO_TARGET_PERCENT must be between 1 and 100")
        return value

    @field_validator("game_streak_challenge_target", "game_shred_challenge_target")
    @classmethod
    def validate_positive_targets(cls, value: int) -> int:
        if value < 1:
            raise ValueError("Game challenge targets must be at least 1")
        return value

    @field_validator("ynab_cache_refresh_interval_minutes")
    @classmethod
    def validate_cache_refresh_interval(cls, value: int) -> int:
        if value < 1:
            raise ValueError("YNAB_CACHE_REFRESH_INTERVAL_MINUTES must be at least 1")
        return value

    @field_validator("ynab_reconciliation_interval_hours")
    @classmethod
    def validate_reconciliation_interval(cls, value: int) -> int:
        if value < 1:
            raise ValueError("YNAB_RECONCILIATION_INTERVAL_HOURS must be at least 1")
        return value

    @field_validator("ynab_reconciliation_lookback_days")
    @classmethod
    def validate_reconciliation_lookback(cls, value: int) -> int:
        if value < 1:
            raise ValueError("YNAB_RECONCILIATION_LOOKBACK_DAYS must be at least 1")
        return value

    @field_validator("twin_recon_hard_fail_delta_abs")
    @classmethod
    def validate_twin_delta_abs(cls, value: float) -> float:
        if value < 0:
            raise ValueError("TWIN_RECON_HARD_FAIL_DELTA_ABS cannot be negative")
        return value

    @field_validator("twin_recon_hard_fail_delta_pct")
    @classmethod
    def validate_twin_delta_pct(cls, value: float) -> float:
        if value < 0:
            raise ValueError("TWIN_RECON_HARD_FAIL_DELTA_PCT cannot be negative")
        return value

    @field_validator("game_water_capacity", "game_bucket_capacity", "game_fire_burn_threshold", "correction_fade_days")
    @classmethod
    def validate_positive_int(cls, value: int) -> int:
        if value < 1:
            raise ValueError("Game/correction numeric settings must be at least 1")
        return value

    @field_validator("game_timezone")
    @classmethod
    def validate_game_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except Exception as exc:
            raise ValueError(f"GAME_TIMEZONE is invalid: {value}") from exc
        return value

    @field_validator("ai_limit_behavior")
    @classmethod
    def validate_ai_limit_behavior(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"hard_fail", "soft_fail"}:
            raise ValueError("AI_LIMIT_BEHAVIOR must be either 'hard_fail' or 'soft_fail'")
        return normalized


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ingest_dir.mkdir(parents=True, exist_ok=True)
    (settings.object_store_root / settings.object_store_receipts_prefix).mkdir(parents=True, exist_ok=True)
    settings.log_file_path.parent.mkdir(parents=True, exist_ok=True)
    settings.debug_tools_flag_file.parent.mkdir(parents=True, exist_ok=True)
    settings.ai_limits_config_path.parent.mkdir(parents=True, exist_ok=True)
    if settings.debug_tools_enabled:
        settings.debug_tools_flag_file.touch(exist_ok=True)
    return settings
