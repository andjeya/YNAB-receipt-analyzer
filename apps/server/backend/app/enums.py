from __future__ import annotations

from enum import Enum


class ReceiptStatus(str, Enum):
    INGESTED = "ingested"
    EXTRACTING = "extracting"
    NEEDS_REVIEW = "needs_review"
    DUPLICATE_REVIEW = "duplicate_review"
    SYNCING = "syncing"
    SYNCED = "synced"
    ERROR_EXTRACT = "error_extract"
    ERROR_SYNC = "error_sync"


class YNABCacheEntityType(str, Enum):
    CATEGORY = "category"
    ACCOUNT = "account"
    PAYEE = "payee"


class YNABSyncStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    MATCHED_UPDATED = "matched_updated"
    CREATED = "created"
    DRY_RUN = "dry_run"
    FAILED = "failed"


class GameReceiptState(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    BROWN = "brown"
    SHREDDED = "shredded"


class GameEventType(str, Enum):
    RECEIPT_CLASSIFIED = "receipt_classified"
    STREAK_INCREMENTED = "streak_incremented"
    STREAK_BROKEN = "streak_broken"
    TOKEN_EARNED = "token_earned"
    TOKEN_SPENT = "token_spent"
    WATER_EARNED = "water_earned"
    WATER_SPENT = "water_spent"
    FIRE_ADDED = "fire_added"
    FIRE_EXTINGUISHED = "fire_extinguished"
    BOARD_BURNED = "board_burned"
    CORRECTION_DETECTED = "correction_detected"
    RESYNC_REQUIRED = "resync_required"


class GameChallengeStatus(str, Enum):
    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
