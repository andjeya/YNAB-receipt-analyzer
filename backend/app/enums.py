from __future__ import annotations

from enum import Enum


class ReceiptStatus(str, Enum):
    INGESTED = "ingested"
    EXTRACTING = "extracting"
    NEEDS_REVIEW = "needs_review"
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
    FAILED = "failed"
