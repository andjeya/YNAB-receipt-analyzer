from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Repo root (in the container image: the WORKDIR /app). config.py lives at
# apps/server/backend/app/config.py, so parents[4] is the project root. Anchor
# all relative data/config paths here so they resolve to the SAME location no
# matter which directory the process is launched from (repo root via dev-up.sh,
# apps/server/backend via the run-app skill, or /app in Docker — where parents[4]
# equals the WORKDIR, making this a no-op). Without this, launching from the
# wrong cwd silently creates a second, divergent ./data dir.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _anchor_path(value: Path) -> Path:
    return value if value.is_absolute() else (_PROJECT_ROOT / value)


def _anchor_sqlite_url(url: str) -> str:
    """Resolve a relative sqlite:/// path against the project root. Non-sqlite
    URLs, already-absolute sqlite paths, and in-memory DBs pass through."""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return url
    raw = url[len(prefix):]
    if raw.startswith("/") or raw.startswith(":memory:"):
        return url
    return f"{prefix}{_anchor_path(Path(raw))}"


class Settings(BaseSettings):
    # env_file is anchored to the project root so the same .env loads regardless
    # of the launch cwd (a missing env_file is simply skipped, e.g. in Docker).
    model_config = SettingsConfigDict(
        env_file=(str(_PROJECT_ROOT / ".env"), str(_PROJECT_ROOT / ".env.local")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

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
    # Soft-deleted receipts are kept this long (so the user can Undo) before a
    # startup sweep hard-deletes the file + rows.
    soft_delete_purge_hours: int = 24

    game_green_hours_threshold: float = 24.0
    game_brown_hours_threshold: float = 72.0
    game_timezone: str = "UTC"
    game_pass_every_green_weeks: int = 4
    game_shred_daily_spend_cap: int = 0
    # How many trailing weeks (including the current one) a validated receipt
    # stays eligible for shredding. 1 = current week only. Runtime-adjustable by
    # the admin via GameSettings.shred_window_weeks; this is the fallback default
    # used when no settings row exists yet.
    game_shred_window_weeks: int = 2
    game_green_ratio_target_percent: int = 70
    game_streak_challenge_target: int = 6
    game_shred_challenge_target: int = 2
    game_water_capacity: int = 5
    # DEPRECATED: no longer gates burns. Burns are now board-pressure driven — a
    # week burns when total active fires exceed your droplets (the worst week goes
    # first). Kept only because it is still surfaced in the `rules` payload; safe to
    # remove once no client reads it.
    game_fire_burn_threshold: int = 3
    correction_fade_days: int = 90

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3-flash-preview"
    gemini_prompt: str = "Categorize receipt line items into the most appropriate YNAB categories."
    gemini_max_retries: int = 3
    ai_model_registry_path: Path = Path(__file__).resolve().parent.parent.parent / "shared/receipt_shared/resources/ai_model_registry.v1.json"
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
    ynab_sync_enabled: bool = False
    ynab_dry_run: bool = True

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

    @field_validator(
        "ingest_dir",
        "object_store_root",
        "log_file_path",
        "debug_tools_flag_file",
        "ai_limits_config_path",
        mode="after",
    )
    @classmethod
    def _anchor_relative_paths(cls, value: Path) -> Path:
        return _anchor_path(value)

    @field_validator("database_url", "ai_usage_db_url", mode="after")
    @classmethod
    def _anchor_sqlite_urls(cls, value: str) -> str:
        return _anchor_sqlite_url(value)

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

    @field_validator("game_pass_every_green_weeks")
    @classmethod
    def validate_pass_every(cls, value: int) -> int:
        if value < 1:
            raise ValueError("GAME_PASS_EVERY_GREEN_WEEKS must be at least 1")
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

    @field_validator("game_water_capacity", "game_fire_burn_threshold", "correction_fade_days")
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
